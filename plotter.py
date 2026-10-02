#!/usr/bin/env python3
"""
plotter.py
Generates one final plot per plot_group defined in sensor_config.yaml.
Called automatically by bootup.py after logging stops.

Plot groups are fully config-driven — no code changes needed when sensors
or groupings change, as long as sensor_config.yaml is updated.
"""

import os
import sys

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
import yaml

matplotlib.use("Agg")  # non-interactive backend — save files only, no display

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG PATHS
# ─────────────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
SENSOR_CONFIG_PATH = os.path.join(_HERE, "config", "sensor_config.yaml")
FILE_CONFIG_PATH   = os.path.join(_HERE, "config", "file.yaml")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _load_yaml(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def _get_latest_csv_path(base_dir: str) -> str:
    """Read the control file written by logger.py to find the last CSV."""
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


def _sensor_col(sensor: dict) -> str:
    """Return the CSV column name for a sensor (must match logger.py header)."""
    return f"{sensor['name']} ({sensor['unit']})"


# ─────────────────────────────────────────────────────────────────────────────
# PLOTTER
# ─────────────────────────────────────────────────────────────────────────────

def generate_plots(csv_path: str, sensor_cfg: dict) -> None:
    """
    Generate one PNG per plot_group defined in sensor_config.yaml.
    Output PNGs are saved next to the CSV.
    """
    print(f"Reading CSV: {csv_path}")

    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print(f"ERROR: CSV not found at {csv_path}")
        return
    except Exception as e:
        print(f"ERROR reading CSV: {e}")
        return

    # Build a lookup: sensor id → sensor definition dict
    sensor_lookup = {s["id"]: s for s in sensor_cfg["sensors"]}

    # Base path for output images (strip .csv extension)
    base_path = os.path.splitext(csv_path)[0]

    plot_groups = sensor_cfg.get("plot_groups", [])
    if not plot_groups:
        print("No plot_groups defined in sensor_config.yaml — nothing to plot.")
        return

    for group in plot_groups:
        group_name   = group["name"]
        ylabel       = group.get("ylabel", "Value")
        sensor_ids   = group.get("sensors", [])

        if not sensor_ids:
            print(f"  Skipping '{group_name}': no sensors listed.")
            continue

        fig, ax = plt.subplots(figsize=(12, 5))

        plotted_any = False
        for sid in sensor_ids:
            sensor = sensor_lookup.get(sid)
            if sensor is None:
                print(f"  WARNING: sensor id '{sid}' in plot group '{group_name}' "
                      f"not found in sensor config — skipping.")
                continue

            col = _sensor_col(sensor)
            if col not in df.columns:
                print(f"  WARNING: column '{col}' not found in CSV — skipping.")
                continue

            ax.plot(df["Time (s)"], df[col], label=sensor["name"])
            plotted_any = True

        if not plotted_any:
            print(f"  No data to plot for group '{group_name}' — skipping.")
            plt.close(fig)
            continue

        ax.set_xlabel("Time (s)")
        ax.set_ylabel(ylabel)
        ax.set_title(group_name)
        ax.legend(loc="upper right")
        ax.grid(True, linestyle="--", alpha=0.5)
        fig.tight_layout()

        # Sanitize group name for filename
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