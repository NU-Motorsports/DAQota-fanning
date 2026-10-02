#!/usr/bin/env python3
"""
plotter.py
Reads raw voltage CSV from logger.py, converts to engineering units
using sensor_config.yaml, and generates one PNG per plot_group.

All conversion happens here so logger.py stays as fast as possible.
"""

import os
import sys

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
import yaml

matplotlib.use("Agg")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG PATHS
# ─────────────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
SENSOR_CONFIG_PATH = os.path.join(_HERE, "sensor_config.yaml")
FILE_CONFIG_PATH   = os.path.join(_HERE, "file.yaml")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _load_yaml(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def _get_latest_csv_path(base_dir: str) -> str:
    control_file = os.path.join(base_dir, "latest_csv_path.txt")
    try:
        with open(control_file, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"ERROR: Control file not found at {control_file}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR reading control file: {e}")
        sys.exit(1)


def _linear_map(series, raw_min, raw_max, unit_min, unit_max):
    """Vectorized 2-point linear map on a pandas Series."""
    raw_min  = float(raw_min)
    raw_max  = float(raw_max)
    unit_min = float(unit_min)
    unit_max = float(unit_max)
    if raw_max == raw_min:
        return series * 0 + unit_min
    return ((series - raw_min) / (raw_max - raw_min)) * (unit_max - unit_min) + unit_min


def _convert_series(sensor: dict, series) -> tuple:
    """
    Convert a raw voltage Series to engineering units.
    Returns (converted_series, unit_label).
    """
    sensor_type = sensor.get("type", "raw")

    if sensor_type == "linear":
        c = sensor["cal"]
        converted = _linear_map(
            series,
            c["raw_min"], c["raw_max"],
            c["unit_min"], c["unit_max"]
        )
        return converted, sensor["unit"]

    elif sensor_type == "custom":
        # Add custom conversions here as needed
        # e.g. if sensor["id"] == "tecat": ...
        return series, sensor["unit"]

    else:  # raw — no conversion
        return series, sensor["unit"]

def save_converted_csv(df: pd.DataFrame, sensor_cfg: dict, csv_path: str) -> None:
    """Save a converted engineering units CSV alongside the raw one."""
    # Build output columns: Time, Timestamp, then eng unit columns
    cols = ["Time (s)", "Timestamp (Eastern)"]
    for sensor in sensor_cfg["sensors"]:
        eng_col = f"{sensor['name']} ({sensor['unit']})"
        if eng_col in df.columns:
            cols.append(eng_col)

    out_path = csv_path.replace("_RAW.csv", "_MAPPED.csv")
    df[cols].to_csv(out_path, index=False)
    print(f"  Converted CSV saved: {out_path}")
# ─────────────────────────────────────────────────────────────────────────────
# PLOTTER
# ─────────────────────────────────────────────────────────────────────────────

def generate_plots(csv_path: str, sensor_cfg: dict) -> None:
    print(f"Reading CSV: {csv_path}")

    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print(f"ERROR: CSV not found at {csv_path}")
        return
    except Exception as e:
        print(f"ERROR reading CSV: {e}")
        return

    # Build sensor lookup
    sensor_lookup = {s["id"]: s for s in sensor_cfg["sensors"]}

    # Convert all raw voltage columns to engineering units
    # Raw column name: "{name} (V)"
    # Converted column name: "{name} ({unit})"
    for sensor in sensor_cfg["sensors"]:
        raw_col = f"{sensor['name']} (V)"
        if raw_col not in df.columns:
            print(f"  WARNING: column '{raw_col}' not in CSV — skipping {sensor['name']}")
            continue
        converted, unit = _convert_series(sensor, df[raw_col])
        eng_col = f"{sensor['name']} ({unit})"
        df[eng_col] = converted

    # Save converted CSV
    save_converted_csv(df, sensor_cfg, csv_path)
    base_path   = os.path.splitext(csv_path)[0]
    plot_groups = sensor_cfg.get("plot_groups", [])

    if not plot_groups:
        print("No plot_groups defined — nothing to plot.")
        return

    for group in plot_groups:
        group_name = group["name"]
        ylabel     = group.get("ylabel", "Value")
        sensor_ids = group.get("sensors", [])

        if not sensor_ids:
            print(f"  Skipping '{group_name}': no sensors listed.")
            continue

        fig, ax = plt.subplots(figsize=(12, 5))
        plotted_any = False

        for sid in sensor_ids:
            sensor = sensor_lookup.get(sid)
            if sensor is None:
                print(f"  WARNING: sensor id '{sid}' not found in config — skipping.")
                continue

            eng_col = f"{sensor['name']} ({sensor['unit']})"
            if eng_col not in df.columns:
                print(f"  WARNING: column '{eng_col}' not in DataFrame — skipping.")
                continue

            ax.plot(df["Time (s)"], df[eng_col], label=sensor["name"])
            plotted_any = True

        if not plotted_any:
            print(f"  No data for group '{group_name}' — skipping.")
            plt.close(fig)
            continue

        ax.set_xlabel("Time (s)")
        ax.set_ylabel(ylabel)
        ax.set_title(group_name)
        ax.legend(loc="upper right")
        ax.grid(True, linestyle="--", alpha=0.5)
        fig.tight_layout()

        safe_name = group_name.upper().replace(" ", "_").replace("/", "_")
        out_path  = f"{base_path}_{safe_name}.png"
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        print(f"  Saved: {out_path}")

    print("Plotting complete.")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sensor_cfg = _load_yaml(SENSOR_CONFIG_PATH)
    file_cfg   = _load_yaml(FILE_CONFIG_PATH)
    base_dir   = file_cfg["base_dir"]
    csv_path   = _get_latest_csv_path(base_dir)
    generate_plots(csv_path, sensor_cfg)