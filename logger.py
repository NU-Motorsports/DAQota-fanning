#!/usr/bin/env python3
"""
logger.py
LabJack T7 DAQ logger — stream mode, raw voltage only.

Logs raw voltages as fast as possible. Engineering unit conversion
happens in plotter.py after the run to keep this loop lean.

Adding a new LINEAR sensor:  add an entry to sensor_config.yaml only.
Adding a NONLINEAR sensor:   add an entry with type: "custom", then add a
                              matching method to plotter.py.
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
SENSOR_CONFIG_PATH = os.path.join(_HERE, "config", "sensor_config.yaml")
FILE_CONFIG_PATH   = os.path.join(_HERE, "config", "file.yaml")

# ─────────────────────────────────────────────────────────────────────────────
# STREAM SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
STREAM_SAMPLE_RATE_HZ = 100
SCANS_PER_READ        = 50
WRITE_BUFFER_SIZE     = 500
PRINT_EVERY_N_SCANS   = 500
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
# LOGGER CLASS
# ═════════════════════════════════════════════════════════════════════════════

class Logger:

    def __init__(self):
        self.sensor_cfg    = self._load_yaml(SENSOR_CONFIG_PATH)
        self.file_cfg      = self._load_yaml(FILE_CONFIG_PATH)
        self.sensors       = self.sensor_cfg["sensors"]

        self.channels      = [s["channel"] for s in self.sensors]
        self.channel_count = len(self.channels)

        self.handle = None

        # File paths
        base_dir  = self.file_cfg["base_dir"]
        save_fp   = str(self.file_cfg["save_fp"]).lstrip(os.sep)
        self.file_dir = os.path.join(base_dir, save_fp)
        os.makedirs(self.file_dir, exist_ok=True)

        timestr = strftime("%m-%d-%Y_%H-%M-%S")
        self.run_name     = f"{timestr}_LJ_DAQ_DATA"
        self.path_raw     = os.path.join(self.file_dir, f"{self.run_name}_RAW.csv")
        self.control_file = os.path.join(base_dir, "latest_csv_path.txt")

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def _configure_ain(self):
        """Set AIN range for each channel — must be called BEFORE stream start."""
        for sensor in self.sensors:
            ch  = sensor["channel"]
            rng = _RANGE_MAP.get(sensor.get("range_v", 10), 10.0)
            ljm.eWriteName(self.handle, f"{ch}_RANGE",            rng)
            ljm.eWriteName(self.handle, f"{ch}_RESOLUTION_INDEX", 0)

    def _build_raw_header(self) -> list:
        """Time + Eastern timestamp + raw voltage column per sensor."""
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
                f.write(self.path_raw)
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

        print(f"\nLogging to:\n  {self.path_raw}")
        print(f"Channels:    {self.channels}")
        print(f"Sample rate: {STREAM_SAMPLE_RATE_HZ} Hz per channel")
        print(f"Total rate:  {STREAM_SAMPLE_RATE_HZ * self.channel_count:,} scans/sec\n")

        # Resolve channel addresses once up front
        ch_addresses = [ljm.nameToAddress(ch)[0] for ch in self.channels]

        # Start stream
        actual_rate = ljm.eStreamStart(
            self.handle,
            SCANS_PER_READ,
            self.channel_count,
            ch_addresses,
            STREAM_SAMPLE_RATE_HZ
        )
        print(f"Stream started at {actual_rate:.1f} Hz per channel\n")

        start         = time()
        scan_count    = 0
        overlap_count = 0
        raw_buf       = deque()

        with open(self.path_raw, "w", newline="") as raw_f:
            raw_w = csv.writer(raw_f)
            raw_w.writerow(self._build_raw_header())

            try:
                while True:
                    # ── HOT LOOP: read and buffer only ────────────────────
                    try:
                        ret = ljm.eStreamRead(self.handle)
                    except ljm.LJMError as e:
                        if e.errorCode == 2942:  # STREAM_SCAN_OVERLAP
                            overlap_count += 1
                            print(f"WARNING: Stream overlap #{overlap_count}")
                            continue
                        raise

                    data      = ret[0]
                    num_scans = len(data) // self.channel_count
                    elapsed   = time() - start
                    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                    for i in range(num_scans):
                        s         = i * self.channel_count
                        scan_time = elapsed - (num_scans - i - 1) / actual_rate
                        voltages  = data[s: s + self.channel_count]
                        raw_buf.append(
                            [f"{scan_time:.6f}", timestamp] +
                            [f"{v:.6f}" for v in voltages]
                        )

                    scan_count += num_scans

                    # ── FLUSH to disk when buffer is full ─────────────────
                    if len(raw_buf) >= WRITE_BUFFER_SIZE:
                        raw_w.writerows(raw_buf)
                        raw_f.flush()
                        raw_buf.clear()

                    # ── STATUS PRINT ──────────────────────────────────────
                    if scan_count % PRINT_EVERY_N_SCANS < num_scans:
                        latest_voltages = data[-self.channel_count:]
                        readings = "  ".join(
                            f"{s['name']}: {v:.4f} V"
                            for s, v in zip(self.sensors, latest_voltages)
                        )
                        print(
                            f"t={elapsed:.1f}s {timestamp}"
                            f"scans={scan_count:,}  overlaps={overlap_count}\n"
                            f"  {readings}"
                        )


            except KeyboardInterrupt:
                print("\nLogging stopped.")

            finally:
                # Flush remaining buffer
                if raw_buf:
                    raw_w.writerows(raw_buf)
                    raw_f.flush()
                    raw_buf.clear()

        print(f"\nTotal scans: {scan_count:,}  Overlaps: {overlap_count}")
        print(f"Raw data saved: {self.path_raw}")


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