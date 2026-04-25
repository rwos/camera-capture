#!/usr/bin/env python3
"""
Appliance-mode recorder for DVX-Nebula analog video capture hat.

Button 1 (GPIO 24, corner of board): start recording
Button 2 (GPIO 23):                  stop recording (if recording) / take still (if idle)
Pi LED  (GPIO 29, active-low):       blinks while recording; brief blink on still saved

Recordings saved to /home/pi/recordings/:
  video_YYYYMMDD_HHMMSS.avi
  still_YYYYMMDD_HHMMSS.jpg
"""

import lgpio
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

BTN_START  = 24        # corner button — start recording
BTN_ACTION = 23        # other button  — stop recording / take still
LED_PIN    = 29        # Pi ACT LED, active-low

RECORDINGS_DIR = Path("/home/pi/recordings")
VIDEO_DEV      = "/dev/video0"
GST_CAPS       = "video/x-raw,format=UYVY,width=720,height=576,framerate=25/1"

DEBOUNCE_S    = 0.05   # 50 ms button debounce
POLL_S        = 0.02   # 20 ms polling interval
BLINK_ON_S    = 0.5    # LED on duration while recording
BLINK_OFF_S   = 0.5    # LED off duration while recording


def gst_record_cmd(path):
    return (
        f"gst-launch-1.0 -e "
        f"v4l2src device={VIDEO_DEV} ! '{GST_CAPS}' ! "
        f"v4l2jpegenc ! avimux ! filesink location={path}"
    )


def gst_still_cmd(path):
    return (
        f"gst-launch-1.0 "
        f"v4l2src device={VIDEO_DEV} num-buffers=1 ! '{GST_CAPS}' ! "
        f"v4l2jpegenc ! filesink location={path}"
    )


class Recorder:
    def __init__(self):
        RECORDINGS_DIR.mkdir(exist_ok=True)

        self.h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_input(self.h, BTN_START,  lgpio.SET_PULL_UP)
        lgpio.gpio_claim_input(self.h, BTN_ACTION, lgpio.SET_PULL_UP)
        lgpio.gpio_claim_output(self.h, LED_PIN, 1)   # LED off initially

        self.proc     = None    # active recording subprocess
        self.rec_path = None

        self._blink_stop = threading.Event()
        self._blink_thread = None

        self._prev = {BTN_START: 1, BTN_ACTION: 1}
        self._last = {BTN_START: 0, BTN_ACTION: 0}

    # ------------------------------------------------------------------
    # LED

    def _led(self, on):
        lgpio.gpio_write(self.h, LED_PIN, 0 if on else 1)  # active-low

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

    def _flash_still(self):
        for _ in range(2):
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
        self.proc = subprocess.Popen(gst_record_cmd(self.rec_path), shell=True)
        self._start_blink()
        print(f"Recording started: {self.rec_path}", flush=True)

    def _stop_recording(self):
        self._stop_blink()
        if self.proc:
            self.proc.terminate()
            self.proc.wait()
            self.proc = None
        print(f"Recording stopped: {self.rec_path}", flush=True)
        self.rec_path = None

    def _capture_still(self):
        path = RECORDINGS_DIR / f"still_{self._timestamp()}.jpg"
        subprocess.run(gst_still_cmd(path), shell=True, timeout=5)
        print(f"Still saved: {path}", flush=True)
        self._flash_still()

    # ------------------------------------------------------------------
    # Main loop

    def _check_button(self, pin, now):
        val = lgpio.gpio_read(self.h, pin)
        pressed = (val == 0 and self._prev[pin] == 1
                   and (now - self._last[pin]) > DEBOUNCE_S)
        if val != self._prev[pin]:
            self._last[pin] = now
        self._prev[pin] = val
        return pressed

    def _check_recording_alive(self):
        if self.proc and self.proc.poll() is not None:
            print("Recording process exited unexpectedly — resetting", flush=True)
            self._stop_blink()
            self.proc = None
            self.rec_path = None

    def run(self):
        print("Recorder ready. Button 1: start recording. Button 2: stop / still.", flush=True)
        try:
            while True:
                now = time.monotonic()

                if self._check_button(BTN_START, now):
                    if self.proc is None:
                        self._start_recording()
                    # already recording — button 1 does nothing

                if self._check_button(BTN_ACTION, now):
                    if self.proc is not None:
                        self._stop_recording()
                    else:
                        self._capture_still()

                self._check_recording_alive()
                time.sleep(POLL_S)

        except KeyboardInterrupt:
            pass
        finally:
            if self.proc is not None:
                self._stop_recording()
            self._led(False)
            lgpio.gpiochip_close(self.h)


if __name__ == "__main__":
    Recorder().run()
