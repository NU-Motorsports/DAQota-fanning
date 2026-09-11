#!/usr/bin/env python3
"""
bootup.py
Production DAQ boot script for Raspberry Pi + LabJack T7.

Behavior:
  - Runs automatically at boot via cron (@reboot)
  - Press button once → connect LabJack, start logging, LED blinks slow
  - Press button again → stop logging, generate plots, LED off
  - If LabJack not found when button pressed → LED blinks fast, waits for retry
  - Ctrl+C exits cleanly

Cron setup (run once):
  crontab -e
  Add this line:
  @reboot sleep 10 && /home/pi/DAQota-fanning/venv/bin/python3 /home/pi/DAQota-fanning/bootup.py >> /home/pi/DAQota-fanning/bootup_log.txt 2>&1
"""

import subprocess
import threading
import time

import RPi.GPIO as GPIO

# ─────────────────────────────────────────────────────────────────────────────
# GPIO SETUP
# ─────────────────────────────────────────────────────────────────────────────
GPIO.setmode(GPIO.BCM)

BUTTON_PIN = 16
LED_PIN    = 21

GPIO.setup(BUTTON_PIN, GPIO.IN,  pull_up_down=GPIO.PUD_UP)
GPIO.setup(LED_PIN,    GPIO.OUT)

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
PYTHON       = "/home/pi/DAQota-fanning/venv/bin/python3"
LOGGER_PATH  = "/home/pi/DAQota-fanning/logger.py"
PLOTTER_PATH = "/home/pi/DAQota-fanning/plotter.py"

# ─────────────────────────────────────────────────────────────────────────────
# SHARED STATE
# ─────────────────────────────────────────────────────────────────────────────
actively_logging = threading.Event()   # set = logging active
error_state      = threading.Event()   # set = LabJack not found, blink fast

print("DAQ bootup starting...")


# ─────────────────────────────────────────────────────────────────────────────
# LED THREAD
#   Error state  → fast blink (0.1s on / 0.1s off)
#   Logging      → slow blink (0.8s on / 0.5s off)
#   Idle         → off
# ─────────────────────────────────────────────────────────────────────────────
def led_blinky() -> None:
    while True:
        if error_state.is_set():
            GPIO.output(LED_PIN, GPIO.HIGH)
            time.sleep(0.1)
            GPIO.output(LED_PIN, GPIO.LOW)
            time.sleep(0.1)
        elif actively_logging.is_set():
            GPIO.output(LED_PIN, GPIO.HIGH)
            time.sleep(0.8)
            GPIO.output(LED_PIN, GPIO.LOW)
            time.sleep(0.5)
        else:
            GPIO.output(LED_PIN, GPIO.LOW)
            time.sleep(0.05)


# ─────────────────────────────────────────────────────────────────────────────
# LABJACK CHECK
# Quick check that the LabJack is reachable before starting the logger.
# ─────────────────────────────────────────────────────────────────────────────
def labjack_is_connected() -> bool:
    try:
        from labjack import ljm
        handle = ljm.openS("T7", "ANY", "ANY")
        ljm.close(handle)
        return True
    except Exception as e:
        print(f"LabJack not found: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOGGING THREAD
# ─────────────────────────────────────────────────────────────────────────────
def log_data() -> None:
    process = None

    while True:

        if actively_logging.is_set():
            if process is None or process.poll() is not None:
                print("Starting data logging...")
                process = subprocess.Popen([PYTHON, LOGGER_PATH])
                time.sleep(1)

        else:
            if process is not None and process.poll() is None:
                print("Stopping data logging...")
                process.terminate()

                try:
                    process.wait(timeout=5)
                    print("Logger terminated.")
                except subprocess.TimeoutExpired:
                    print("Logger did not stop in time — killing.")
                    process.kill()
                    process.wait()

                process = None

                print("Generating plots...")
                try:
                    subprocess.run([PYTHON, PLOTTER_PATH], check=True)
                    print("Plots saved.")
                except subprocess.CalledProcessError as e:
                    print(f"Plotter error: {e}")
                except Exception as e:
                    print(f"Unexpected plotter error: {e}")

            if process is None:
                actively_logging.wait()
                continue

        time.sleep(0.1)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    threading.Thread(target=led_blinky, daemon=True).start()
    threading.Thread(target=log_data,   daemon=True).start()

    previous_button_state = GPIO.input(BUTTON_PIN)
    is_logging = False
    print("Ready. Waiting for button press...")

    try:
        while True:
            current_button_state = GPIO.input(BUTTON_PIN)

            if previous_button_state == GPIO.HIGH and current_button_state == GPIO.LOW:

                if error_state.is_set():
                    # Retry LabJack connection
                    print("Retrying LabJack connection...")
                    if labjack_is_connected():
                        error_state.clear()
                        is_logging = True
                        actively_logging.set()
                        print("LabJack found. Logging started.")
                    else:
                        print("LabJack still not found.")

                elif not is_logging:
                    # Start logging
                    print("Button pressed → checking LabJack...")
                    if labjack_is_connected():
                        error_state.clear()
                        is_logging = True
                        actively_logging.set()
                        print("Logging started.")
                    else:
                        print("LabJack not found. Fast blink active. Press button to retry.")
                        error_state.set()

                else:
                    # Stop logging
                    print("Button pressed → stopping logging.")
                    is_logging = False
                    error_state.clear()
                    actively_logging.clear()

            previous_button_state = current_button_state
            time.sleep(0.02)

    except KeyboardInterrupt:
        print("\nShutting down.")

    finally:
        actively_logging.clear()
        time.sleep(0.5)
        GPIO.output(LED_PIN, GPIO.LOW)
        GPIO.cleanup()


if __name__ == "__main__":
    main()
