#!/usr/bin/env python3
"""
Appliance-mode recorder for ADV7280 uHat Shield analog video capture.

Button (GPIO 24, physical pin 18): toggle recording (start if idle, stop if recording)
External LED (GPIO 16, physical pin 36): blinks while recording; double-blinks while idle

Recordings saved to /home/pi/recordings/:
  video_YYYYMMDD_HHMMSS.avi

Usage:
  recorder.py           -- appliance mode (button-driven, runs forever)
  recorder.py --stream  -- stream MJPEG to stdout; pipe to ffplay on the Mac
"""

import lgpio
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

BTN_TOGGLE = 24        # physical pin 18 -- toggle recording
LED_GPIO   = 16        # physical pin 36 -- external red LED, active-high, 150Ω to anode
                       # original: Pi ACT LED via sysfs /sys/class/leds/ACT/brightness

RECORDINGS_DIR = Path("/home/pi/recordings")
VIDEO_DEV      = "/dev/video0"
# V4L2 driver reports 25fps (PAL field rate) via timeperframe; with I2P the hardware
# actually outputs 50fps. Omit framerate from v4l2src caps so GStreamer negotiation
# succeeds, then use capssetter to set the correct rate downstream for the AVI header.
# For NTSC: height=507, GST_FRAMERATE="60000/1001". Without I2P: halve the framerate.
GST_CAPS       = "video/x-raw,format=UYVY,width=720,height=576"  # PAL; NTSC: height=507
GST_FRAMERATE  = "50/1"    # PAL + I2P; NTSC + I2P: 60000/1001

DEBOUNCE_S        = 0.05   # 50 ms button debounce
POLL_S            = 0.02   # 20 ms polling interval
BLINK_ON_S        = 0.5    # LED on duration while recording
BLINK_OFF_S       = 0.5    # LED off duration while recording
HEARTBEAT_S       = 5.0    # pause between idle double-blinks
HEARTBEAT_BLINK_S = 0.04   # each pulse in the idle double-blink
HEARTBEAT_GAP_S   = 0.08   # gap between the two pulses
STARTUP_BLINKS    = 3      # number of blinks on startup


def gst_record_cmd(path):
    return (
        f"gst-launch-1.0 -e "
        f"v4l2src device={VIDEO_DEV} ! '{GST_CAPS}' ! "
        f"capssetter caps='video/x-raw,framerate={GST_FRAMERATE}' ! "
        f"v4l2jpegenc ! avimux ! filesink location='{path}'"
    )


def gst_stream_cmd():
    return (
        f"gst-launch-1.0 -q -e "
        f"v4l2src device={VIDEO_DEV} ! '{GST_CAPS}' ! "
        f"capssetter caps='video/x-raw,framerate={GST_FRAMERATE}' ! "
        f"v4l2jpegenc ! fdsink fd=1"
    )


class Recorder:
    def __init__(self):
        RECORDINGS_DIR.mkdir(exist_ok=True)

        self.h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_input(self.h, BTN_TOGGLE, lgpio.SET_PULL_UP)
        lgpio.gpio_claim_output(self.h, LED_GPIO, 0)

        self.proc     = None    # active recording subprocess
        self.rec_path = None

        self._blink_stop = threading.Event()
        self._blink_thread = None

        self._prev  = {BTN_TOGGLE: lgpio.gpio_read(self.h, BTN_TOGGLE)}
        self._last  = {BTN_TOGGLE: time.monotonic()}
        self._fired = {BTN_TOGGLE: False}

    # ------------------------------------------------------------------
    # LED

    def _led(self, on):
        lgpio.gpio_write(self.h, LED_GPIO, 1 if on else 0)

    def _blink_loop(self):
        while not self._blink_stop.is_set():
            self._led(True)
            if self._blink_stop.wait(BLINK_ON_S):
                break
            self._led(False)
            self._blink_stop.wait(BLINK_OFF_S)
        self._led(False)

    def _start_blink(self):
        self._blink_stop.clear()
        self._blink_thread = threading.Thread(target=self._blink_loop, daemon=True)
        self._blink_thread.start()

    def _stop_blink(self):
        self._blink_stop.set()
        if self._blink_thread:
            self._blink_thread.join()
            self._blink_thread = None

    def _flash_startup(self):
        for _ in range(STARTUP_BLINKS):
            self._led(True)
            time.sleep(0.1)
            self._led(False)
            time.sleep(0.1)

    # ------------------------------------------------------------------
    # Recording

    def _timestamp(self):
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _start_recording(self):
        self.rec_path = RECORDINGS_DIR / f"video_{self._timestamp()}.avi"
        self.proc = subprocess.Popen(
            gst_record_cmd(self.rec_path), shell=True, start_new_session=True)
        self._start_blink()
        print(f"Recording started: {self.rec_path}", flush=True)

    def _stop_recording(self):
        self._stop_blink()
        if self.proc:
            os.killpg(self.proc.pid, signal.SIGINT)  # SIGINT triggers -e EOS flush
            self.proc.wait()
            self.proc = None
        print(f"Recording stopped: {self.rec_path}", flush=True)
        self.rec_path = None

    # ------------------------------------------------------------------
    # Main loop

    def _check_button(self, now):
        val = lgpio.gpio_read(self.h, BTN_TOGGLE)
        if val != self._prev[BTN_TOGGLE]:
            self._prev[BTN_TOGGLE] = val
            self._last[BTN_TOGGLE] = now
            if val == 1:
                self._fired[BTN_TOGGLE] = False  # reset on release
            return False
        # fire once when button has been stably LOW for the debounce period
        if val == 0 and not self._fired[BTN_TOGGLE] and (now - self._last[BTN_TOGGLE]) >= DEBOUNCE_S:
            self._fired[BTN_TOGGLE] = True
            return True
        return False

    def _check_recording_alive(self):
        if self.proc and self.proc.poll() is not None:
            print("Recording process exited unexpectedly -- resetting", flush=True)
            self._stop_blink()
            self.proc = None
            self.rec_path = None

    def run(self):
        self._flash_startup()
        print("Recorder ready. Button: toggle recording.", flush=True)
        next_heartbeat = time.monotonic() + HEARTBEAT_S
        try:
            while True:
                now = time.monotonic()

                if self._check_button(now):
                    if self.proc is None:
                        self._start_recording()
                    else:
                        self._stop_recording()

                self._check_recording_alive()

                if self.proc is None and now >= next_heartbeat:
                    self._led(True);  time.sleep(HEARTBEAT_BLINK_S); self._led(False)
                    time.sleep(HEARTBEAT_GAP_S)
                    self._led(True);  time.sleep(HEARTBEAT_BLINK_S); self._led(False)
                    next_heartbeat = now + HEARTBEAT_S

                time.sleep(POLL_S)

        except KeyboardInterrupt:
            pass
        finally:
            if self.proc is not None:
                self._stop_recording()
            self._led(False)
            lgpio.gpiochip_close(self.h)


if __name__ == "__main__":
    if "--stream" in sys.argv:
        subprocess.run(gst_stream_cmd(), shell=True)
    else:
        Recorder().run()
