#!/usr/bin/env python3
"""
logger.py
LabJack T7 DAQ logger — stream mode, config-driven via sensor_config.yaml.

Stream mode delivers true hardware-timed samples at up to 100kHz total.
For 13 channels, max per-channel rate = ~7,692 Hz (100kHz / 13).
Default is set to 5,000 Hz per channel (65,000 rows/sec) with a write
buffer to avoid hammering the SD card.

Adding a new LINEAR sensor:  add an entry to sensor_config.yaml only.
Adding a NONLINEAR sensor:   add an entry with type: "custom", then add a
                              matching method to the CUSTOM CONVERSIONS section
                              and register it in _build_custom_map().
"""

import csv
import os
import shutil
import yaml
from collections import deque
from datetime import datetime
from time import time, strftime

from labjack import ljm

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG PATHS
# ─────────────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
SENSOR_CONFIG_PATH = os.path.join(_HERE, "sensor_config.yaml")
FILE_CONFIG_PATH   = os.path.join(_HERE, "file.yaml")

# ─────────────────────────────────────────────────────────────────────────────
# STREAM SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
STREAM_SAMPLE_RATE_HZ = 5000   # per channel — 13ch × 5000 = 65,000 scans/sec
SCANS_PER_READ        = 1000   # how many scans to pull per ljm.eStreamRead call
WRITE_BUFFER_SIZE     = 5000   # flush to CSV after this many rows accumulate
PRINT_EVERY_N_SCANS   = 5000   # print to terminal every N scans (reduce spam)

# ─────────────────────────────────────────────────────────────────────────────
# RANGE CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
_RANGE_MAP = {
    10:   10.0,
    1:    1.0,
    0.1:  0.1,
    0.01: 0.01,
}


# ═════════════════════════════════════════════════════════════════════════════
# CUSTOM CONVERSIONS
# ═════════════════════════════════════════════════════════════════════════════

def _convert_tecat(voltage: float) -> float:
    """Placeholder custom conversion for Tecat sensor."""
    return voltage


def _build_custom_map() -> dict:
    return {
        "tecat": _convert_tecat,
    }


# ═════════════════════════════════════════════════════════════════════════════
# LOGGER CLASS
# ═════════════════════════════════════════════════════════════════════════════

class Logger:

    def __init__(self):
        self.sensor_cfg    = self._load_yaml(SENSOR_CONFIG_PATH)
        self.file_cfg      = self._load_yaml(FILE_CONFIG_PATH)
        self.sensors       = self.sensor_cfg["sensors"]
        self.custom_map    = _build_custom_map()

        self.channels      = [s["channel"] for s in self.sensors]
        self.channel_count = len(self.channels)

        self.handle = None

        # File paths
        base_dir  = self.file_cfg["base_dir"]
        save_fp   = str(self.file_cfg["save_fp"]).lstrip(os.sep)
        self.file_dir = os.path.join(base_dir, save_fp)
        os.makedirs(self.file_dir, exist_ok=True)

        timestr = strftime("%m-%d-%Y_%H-%M-%S")
        self.run_name    = f"{timestr}_LJ_DAQ_DATA"
        self.path_mapped = os.path.join(self.file_dir, f"{self.run_name}_MAPPED.csv")
        self.path_raw    = os.path.join(self.file_dir, f"{self.run_name}_RAW.csv")
        self.control_file = os.path.join(base_dir, "latest_csv_path.txt")

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return yaml.safe_load(f)

    @staticmethod
    def _linear_map(voltage, raw_min, raw_max, unit_min, unit_max) -> float:
        voltage  = float(voltage)
        raw_min  = float(raw_min)
        raw_max  = float(raw_max)
        unit_min = float(unit_min)
        unit_max = float(unit_max)
        if raw_max == raw_min:
            return unit_min
        return ((voltage - raw_min) / (raw_max - raw_min)) * (unit_max - unit_min) + unit_min

    def _convert(self, sensor: dict, voltage: float) -> float:
        sensor_type = sensor.get("type", "raw")
        if sensor_type == "linear":
            c = sensor["cal"]
            return self._linear_map(
                voltage,
                c["raw_min"], c["raw_max"],
                c["unit_min"], c["unit_max"]
            )
        elif sensor_type == "custom":
            fn = self.custom_map.get(sensor["id"])
            if fn is None:
                raise ValueError(f"No custom function for sensor '{sensor['id']}'")
            return fn(voltage)
        else:
            return voltage

    def _configure_ain(self):
        """Set AIN range for each channel."""
        for sensor in self.sensors:
            ch  = sensor["channel"]
            rng = _RANGE_MAP.get(sensor.get("range_v", 10), 10.0)
            ljm.eWriteName(self.handle, f"{ch}_RANGE",            rng)
            ljm.eWriteName(self.handle, f"{ch}_RESOLUTION_INDEX", 0)

    def _build_header(self) -> list:
        return ["Time (s)", "Timestamp (Eastern)"] + [
            f"{s['name']} ({s['unit']})" for s in self.sensors
        ]

    def _build_raw_header(self) -> list:
        return ["Time (s)", "Timestamp (Eastern)"] + [
            f"{s['name']} (V)" for s in self.sensors
        ]

    # ─────────────────────────────────────────────────────────────────────────
    # CONNECT / DISCONNECT
    # ─────────────────────────────────────────────────────────────────────────

    def connect(self):
        print("Connecting to LabJack T7...")
        self.handle = ljm.openS("T7", "ANY", "ANY")
        info = ljm.getHandleInfo(self.handle)
        print(f"Connected to LabJack T7 — S/N {info[2]}")
        self._configure_ain()

    def disconnect(self):
        if self.handle is not None:
            try:
                ljm.eStreamStop(self.handle)
            except Exception:
                pass
            try:
                ljm.close(self.handle)
            except Exception:
                pass
            self.handle = None
            print("LabJack disconnected.")

    # ─────────────────────────────────────────────────────────────────────────
    # FILE SETUP
    # ─────────────────────────────────────────────────────────────────────────

    def _write_control_file(self):
        try:
            with open(self.control_file, "w") as f:
                f.write(self.path_mapped)
        except Exception as e:
            print(f"WARNING: Could not write control file: {e}")

    def _save_config_copy(self):
        dest = os.path.join(self.file_dir, f"{self.run_name}.yaml")
        try:
            shutil.copy(SENSOR_CONFIG_PATH, dest)
        except Exception as e:
            print(f"WARNING: Could not copy config: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # STREAM LOGGING
    # ─────────────────────────────────────────────────────────────────────────

    def run(self):
        self._write_control_file()
        self._save_config_copy()

        print(f"\nLogging to:\n  {self.path_mapped}\n  {self.path_raw}")
        print(f"Channels:    {self.channels}")
        print(f"Sample rate: {STREAM_SAMPLE_RATE_HZ} Hz per channel")
        print(f"Total rate:  {STREAM_SAMPLE_RATE_HZ * self.channel_count:,} scans/sec\n")

        # Start stream
        actual_rate = ljm.eStreamStart(
            self.handle,
            SCANS_PER_READ,
            self.channel_count,
            [ljm.nameToAddress(ch)[0] for ch in self.channels],
            STREAM_SAMPLE_RATE_HZ
        )
        print(f"Stream started at {actual_rate:.1f} Hz per channel")

        start      = time()
        scan_count = 0
        mapped_buf = deque()
        raw_buf    = deque()

        with (
            open(self.path_mapped, "w", newline="") as mapped_f,
            open(self.path_raw,    "w", newline="") as raw_f,
        ):
            mapped_w = csv.writer(mapped_f)
            raw_w    = csv.writer(raw_f)
            mapped_w.writerow(self._build_header())
            raw_w.writerow(self._build_raw_header())

            try:
                while True:
                    # Read a chunk of scans from the stream buffer
                    ret = ljm.eStreamRead(self.handle)
                    data        = ret[0]   # flat list: [ch0_s0, ch1_s0, ..., ch0_s1, ch1_s1, ...]
                    num_scans   = len(data) // self.channel_count
                    elapsed     = time() - start
                    timestamp   = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                    # Split flat data into per-scan rows
                    for i in range(num_scans):
                        scan_start = i * self.channel_count
                        voltages   = data[scan_start: scan_start + self.channel_count]

                        # Time for this specific scan (interpolate within the chunk)
                        scan_time = elapsed - (num_scans - i - 1) / actual_rate

                        mapped_vals = [
                            self._convert(sensor, v)
                            for sensor, v in zip(self.sensors, voltages)
                        ]

                        mapped_buf.append(
                            [f"{scan_time:.6f}", timestamp] + [f"{v:.6f}" for v in mapped_vals]
                        )
                        raw_buf.append(
                            [f"{scan_time:.6f}", timestamp] + [f"{v:.6f}" for v in voltages]
                        )

                    scan_count += num_scans

                    # Flush buffer to disk
                    if len(mapped_buf) >= WRITE_BUFFER_SIZE:
                        mapped_w.writerows(mapped_buf)
                        raw_w.writerows(raw_buf)
                        mapped_f.flush()
                        raw_f.flush()
                        mapped_buf.clear()
                        raw_buf.clear()

                    # Print status periodically
                    if scan_count % PRINT_EVERY_N_SCANS < num_scans:
                        latest = [self._convert(s, v) for s, v in zip(self.sensors, voltages)]
                        print(f"t={elapsed:.2f}s  {timestamp}  scans={scan_count:,}", end="  ")
                        for sensor, val in zip(self.sensors, latest):
                            print(f"{sensor['name']}: {val:.3f} {sensor['unit']}", end="  ")
                        print()

            except KeyboardInterrupt:
                print("\nLogging stopped.")

            finally:
                # Flush remaining buffer
                if mapped_buf:
                    mapped_w.writerows(mapped_buf)
                    raw_w.writerows(raw_buf)
                    mapped_f.flush()
                    raw_f.flush()

        print(f"\nTotal scans: {scan_count:,}")
        print(f"Data saved:\n  {self.path_mapped}\n  {self.path_raw}")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger = Logger()
    try:
        logger.connect()
        logger.run()
    finally:
        logger.disconnect()