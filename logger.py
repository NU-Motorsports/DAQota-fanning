#!/usr/bin/env python3
"""
logger.py
LabJack T7 DAQ logger — fully config-driven via config/sensor_config.yaml.

Adding a new LINEAR sensor:  add an entry to sensor_config.yaml only.
Adding a NONLINEAR sensor:   add an entry with type: "custom", then add a
                              matching method to the CUSTOM CONVERSIONS section
                              below and register it in _build_custom_map().
"""

import csv
import os
import shutil
import yaml
from time import time, strftime

from labjack import ljm

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG PATHS  (relative to this file's location)
# ─────────────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
SENSOR_CONFIG_PATH = os.path.join(_HERE, "config", "sensor_config.yaml")
FILE_CONFIG_PATH   = os.path.join(_HERE, "config", "file.yaml")

# ─────────────────────────────────────────────────────────────────────────────
# RANGE CONSTANTS  (LabJack T7 AIN range register values)
# ─────────────────────────────────────────────────────────────────────────────
_RANGE_MAP = {
    10:   10.0,
    1:    1.0,
    0.1:  0.1,
    0.01: 0.01,
}


# ═════════════════════════════════════════════════════════════════════════════
# CUSTOM CONVERSIONS
# Add one function per custom-type sensor.  Each function receives the raw
# voltage (float) and returns the converted engineering value (float).
# Register new functions in _build_custom_map() below.
# ═════════════════════════════════════════════════════════════════════════════

def _convert_tecat(voltage: float) -> float:
    """Placeholder custom conversion for Tecat sensor."""
    # TODO: implement actual Tecat conversion once spec is confirmed
    return voltage


def _build_custom_map() -> dict:
    """Map sensor id → custom conversion function."""
    return {
        "tecat": _convert_tecat,
        # "imu": _convert_imu,   # add future custom sensors here
    }


# ═════════════════════════════════════════════════════════════════════════════
# LOGGER CLASS
# ═════════════════════════════════════════════════════════════════════════════

class Logger:
    """
    Reads all sensors defined in sensor_config.yaml from a LabJack T7,
    converts voltages to engineering units, and writes mapped + raw CSVs.
    """

    def __init__(self):
        self.sensor_cfg   = self._load_yaml(SENSOR_CONFIG_PATH)
        self.file_cfg     = self._load_yaml(FILE_CONFIG_PATH)
        self.sensors      = self.sensor_cfg["sensors"]
        self.sample_rate  = self.sensor_cfg.get("sample_rate_hz", 1000)
        self.custom_map   = _build_custom_map()

        self.channels     = [s["channel"] for s in self.sensors]
        self.channel_count = len(self.channels)

        self.handle = None   # LabJack device handle, set in connect()

        # Build file paths once
        base_dir  = self.file_cfg["base_dir"]
        save_fp   = str(self.file_cfg["save_fp"]).lstrip(os.sep)
        self.file_dir = os.path.join(base_dir, save_fp)
        os.makedirs(self.file_dir, exist_ok=True)

        timestr = strftime("%m-%d-%Y_%H-%M-%S")
        self.run_name      = f"{timestr}_LJ_DAQ_DATA"
        self.path_mapped   = os.path.join(self.file_dir, f"{self.run_name}_MAPPED.csv")
        self.path_raw      = os.path.join(self.file_dir, f"{self.run_name}_RAW.csv")
        self.control_file  = os.path.join(base_dir, "latest_csv_path.txt")

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return yaml.safe_load(f)

    @staticmethod
    def _linear_map(voltage, raw_min, raw_max, unit_min, unit_max) -> float:
        """2-point linear map: voltage → engineering units."""
        voltage  = float(voltage)
        raw_min  = float(raw_min)
        raw_max  = float(raw_max)
        unit_min = float(unit_min)
        unit_max = float(unit_max)
        if raw_max == raw_min:
            return unit_min
        return ((voltage - raw_min) / (raw_max - raw_min)) * (unit_max - unit_min) + unit_min

    def _convert(self, sensor: dict, voltage: float) -> float:
        """Convert raw voltage to engineering units based on sensor type."""
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
                raise ValueError(
                    f"Sensor '{sensor['id']}' has type 'custom' but no matching "
                    f"function is registered in _build_custom_map()."
                )
            return fn(voltage)

        else:  # "raw" — pass through unchanged
            return voltage

    def _configure_ain(self):
        """Set AIN range and resolution for each channel on the T7."""
        for sensor in self.sensors:
            ch   = sensor["channel"]
            rng  = _RANGE_MAP.get(sensor.get("range_v", 10), 10.0)
            # Range register: e.g. AIN0_RANGE
            ljm.eWriteName(self.handle, f"{ch}_RANGE",      rng)
            ljm.eWriteName(self.handle, f"{ch}_RESOLUTION_INDEX", 0)  # 0 = default

    # ─────────────────────────────────────────────────────────────────────────
    # CONNECT / DISCONNECT
    # ─────────────────────────────────────────────────────────────────────────

    def connect(self):
        """Open connection to the LabJack T7."""
        print("Connecting to LabJack T7...")
        self.handle = ljm.openS("T7", "ANY", "ANY")
        info = ljm.getHandleInfo(self.handle)
        print(f"Connected to LabJack T7 — S/N {info[2]}")
        self._configure_ain()

    def disconnect(self):
        """Close connection to the LabJack T7."""
        if self.handle is not None:
            ljm.close(self.handle)
            self.handle = None
            print("LabJack disconnected.")

    # ─────────────────────────────────────────────────────────────────────────
    # FILE SETUP
    # ─────────────────────────────────────────────────────────────────────────

    def _write_control_file(self):
        """Write the mapped CSV path so plotter.py knows where to find it."""
        try:
            with open(self.control_file, "w") as f:
                f.write(self.path_mapped)
            print(f"Control file updated: {self.control_file}")
        except Exception as e:
            print(f"WARNING: Could not write control file: {e}")

    def _save_config_copy(self):
        """Copy sensor_config.yaml alongside the CSV for traceability."""
        dest = os.path.join(self.file_dir, f"{self.run_name}.yaml")
        try:
            shutil.copy(SENSOR_CONFIG_PATH, dest)
            print(f"Config copy saved: {dest}")
        except Exception as e:
            print(f"WARNING: Could not copy config file: {e}")

    def _build_header(self) -> list:
        """CSV column header: Time + one column per sensor (name + unit)."""
        return ["Time (s)"] + [
            f"{s['name']} ({s['unit']})" for s in self.sensors
        ]

    def _build_raw_header(self) -> list:
        """Raw CSV column header: Time + one voltage column per sensor."""
        return ["Time (s)"] + [
            f"{s['name']} (V)" for s in self.sensors
        ]

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN LOGGING LOOP
    # ─────────────────────────────────────────────────────────────────────────

    def run(self):
        """
        Acquire data at sample_rate_hz and write mapped + raw CSVs until
        KeyboardInterrupt or the calling thread terminates the process.
        """
        self._write_control_file()
        self._save_config_copy()

        print(f"\nLogging to:\n  {self.path_mapped}\n  {self.path_raw}")
        print(f"Channels: {self.channels}")
        print(f"Rate:     {self.sample_rate} Hz\n")

        interval = 1.0 / self.sample_rate
        start    = time()

        with (
            open(self.path_mapped, "w", newline="") as mapped_f,
            open(self.path_raw,    "w", newline="") as raw_f,
        ):
            mapped_w = csv.writer(mapped_f)
            raw_w    = csv.writer(raw_f)

            mapped_w.writerow(self._build_header())
            raw_w.writerow(self._build_raw_header())
            mapped_f.flush()
            raw_f.flush()

            try:
                while True:
                    loop_start = time()

                    # Read all channels in one round-trip
                    voltages = ljm.eReadNames(self.handle, self.channel_count, self.channels)

                    elapsed = time() - start

                    # Convert voltages → engineering units
                    mapped_vals = [
                        self._convert(sensor, v)
                        for sensor, v in zip(self.sensors, voltages)
                    ]

                    # Print to terminal
                    print(f"t={elapsed:.3f}s", end="  ")
                    for sensor, raw, mapped in zip(self.sensors, voltages, mapped_vals):
                        print(f"{sensor['name']}: {raw:.4f}V → {mapped:.4f} {sensor['unit']}", end="  ")
                    print()

                    # Write rows
                    mapped_w.writerow([f"{elapsed:.6f}"] + [f"{v:.6f}" for v in mapped_vals])
                    raw_w.writerow(   [f"{elapsed:.6f}"] + [f"{v:.6f}" for v in voltages])
                    mapped_f.flush()
                    raw_f.flush()

                    # Pace to target sample rate
                    sleep_time = interval - (time() - loop_start)
                    if sleep_time > 0:
                        import time as _t
                        _t.sleep(sleep_time)

            except KeyboardInterrupt:
                print("\nLogging stopped by keyboard interrupt.")

        print(f"\nData saved:\n  {self.path_mapped}\n  {self.path_raw}")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT  (called by bootup.py via subprocess)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger = Logger()
    try:
        logger.connect()
        logger.run()
    finally:
        logger.disconnect()
