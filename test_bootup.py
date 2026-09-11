#!/usr/bin/env python3
"""
test_bootup.py
Tests button, LED, and LabJack T7 connection without logging any data.

- Press button once: LED blinks, LabJack connection opens and prints live voltages
- Press button again: LED off, LabJack disconnects
- Ctrl+C to exit cleanly
"""

import threading
import time

import RPi.GPIO as GPIO
from labjack import ljm

# ─────────────────────────────────────────────────────────────────────────────
# GPIO SETUP
# ─────────────────────────────────────────────────────────────────────────────
GPIO.setmode(GPIO.BCM)

BUTTON_PIN = 16
LED_PIN    = 21

GPIO.setup(BUTTON_PIN, GPIO.IN,  pull_up_down=GPIO.PUD_UP)
GPIO.setup(LED_PIN,    GPIO.OUT)

# ─────────────────────────────────────────────────────────────────────────────
# SHARED STATE
# ─────────────────────────────────────────────────────────────────────────────
actively_running = threading.Event()


# ─────────────────────────────────────────────────────────────────────────────
# LED THREAD
# ─────────────────────────────────────────────────────────────────────────────
def led_blinky() -> None:
    while True:
        if actively_running.is_set():
            GPIO.output(LED_PIN, GPIO.HIGH)
            time.sleep(0.8)
            GPIO.output(LED_PIN, GPIO.LOW)
            time.sleep(0.5)
        else:
            GPIO.output(LED_PIN, GPIO.LOW)
            actively_running.wait()


# ─────────────────────────────────────────────────────────────────────────────
# LABJACK THREAD
# Opens connection and prints live voltages while active, disconnects when stopped
# ─────────────────────────────────────────────────────────────────────────────
def labjack_test() -> None:
    # Channels to read — update to match your wired sensors
    channels = ["AIN0", "AIN1", "AIN2", "AIN3", "AIN4"]

    while True:
        actively_running.wait()  # block until button pressed

        handle = None
        try:
            print("Connecting to LabJack T7...")
            handle = ljm.openS("T7", "ANY", "ANY")
            info = ljm.getHandleInfo(handle)
            print(f"Connected to LabJack T7 — S/N {info[2]}")

            while actively_running.is_set():
                voltages = ljm.eReadNames(handle, len(channels), channels)
                readings = "  ".join(
                    f"{ch}: {v:.4f}V" for ch, v in zip(channels, voltages)
                )
                print(f"  {readings}")
                time.sleep(0.5)

        except Exception as e:
            print(f"LabJack error: {e}")

        finally:
            if handle is not None:
                ljm.close(handle)
                print("LabJack disconnected.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    print("Test bootup starting...")
    print("Press button to connect LabJack and blink LED.")
    print("Press again to stop. Ctrl+C to exit.\n")

    threading.Thread(target=led_blinky,    daemon=True).start()
    threading.Thread(target=labjack_test,  daemon=True).start()

    previous_button_state = GPIO.input(BUTTON_PIN)
    toggle = False

    try:
        while True:
            current_button_state = GPIO.input(BUTTON_PIN)

            if previous_button_state == GPIO.HIGH and current_button_state == GPIO.LOW:
                toggle = not toggle
                if toggle:
                    print("Button pressed → starting.")
                    actively_running.set()
                else:
                    print("Button pressed → stopping.")
                    actively_running.clear()

            previous_button_state = current_button_state
            time.sleep(0.02)

    except KeyboardInterrupt:
        print("\nExiting.")

    finally:
        GPIO.output(LED_PIN, GPIO.LOW)
        GPIO.cleanup()


if __name__ == "__main__":
    main()