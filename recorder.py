#!/usr/bin/env python3
"""
Appliance-mode recorder for DVX-Nebula analog video capture hat.

Button 1 (GPIO 24, corner of board): start/stop video recording
Button 2 (GPIO 23):                  capture a still image
Pi LED  (GPIO 29, active-low):       lit while recording; double-flash on still saved

Recordings are saved to /home/pi/recordings/ with timestamped filenames:
  video_YYYYMMDD_HHMMSS.avi
  still_YYYYMMDD_HHMMSS.jpg

Still capture while recording: recording is briefly interrupted (one gap in the file),
a still is saved, then recording resumes automatically.
"""

import lgpio
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BTN_RECORD = 24        # corner button — toggle recording
BTN_STILL  = 23        # other button  — save a still
LED_PIN    = 29        # Pi ACT LED, active-low

RECORDINGS_DIR = Path("/home/pi/recordings")
VIDEO_DEV      = "/dev/video0"
GST_CAPS       = "video/x-raw,format=UYVY,width=720,height=576,framerate=25/1"

DEBOUNCE_S = 0.05      # 50 ms button debounce
POLL_S     = 0.02      # 20 ms polling interval


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
        lgpio.gpio_claim_input(self.h, BTN_RECORD, lgpio.SET_PULL_UP)
        lgpio.gpio_claim_input(self.h, BTN_STILL,  lgpio.SET_PULL_UP)
        lgpio.gpio_claim_output(self.h, LED_PIN, 1)   # start with LED off

        self.proc      = None   # active recording subprocess
        self.rec_path  = None

        self._prev  = {BTN_RECORD: 1, BTN_STILL: 1}
        self._last  = {BTN_RECORD: 0, BTN_STILL: 0}

    # ------------------------------------------------------------------
    # LED helpers

    def _led(self, on):
        lgpio.gpio_write(self.h, LED_PIN, 0 if on else 1)   # active-low

    def _flash(self, count=2, interval=0.1):
        was_recording = self.proc is not None
        for _ in range(count):
            self._led(True)
            time.sleep(interval)
            self._led(False)
            time.sleep(interval)
        self._led(was_recording)

    # ------------------------------------------------------------------
    # Recording

    def _timestamp(self):
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _start_recording(self):
        self.rec_path = RECORDINGS_DIR / f"video_{self._timestamp()}.avi"
        self.proc = subprocess.Popen(
            gst_record_cmd(self.rec_path),
            shell=True,
        )
        self._led(True)
        print(f"Recording started: {self.rec_path}", flush=True)

    def _stop_recording(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait()
            self.proc = None
        self._led(False)
        print(f"Recording stopped: {self.rec_path}", flush=True)
        self.rec_path = None

    def _capture_still(self):
        was_recording = self.proc is not None
        if was_recording:
            self._stop_recording()
            time.sleep(0.1)   # let V4L2 device release

        path = RECORDINGS_DIR / f"still_{self._timestamp()}.jpg"
        subprocess.run(gst_still_cmd(path), shell=True, timeout=5)
        print(f"Still saved: {path}", flush=True)
        self._flash(count=2)

        if was_recording:
            time.sleep(0.1)
            self._start_recording()

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
            self.proc = None
            self.rec_path = None
            self._led(False)

    def run(self):
        print("Recorder ready.", flush=True)
        try:
            while True:
                now = time.monotonic()

                if self._check_button(BTN_RECORD, now):
                    if self.proc is None:
                        self._start_recording()
                    else:
                        self._stop_recording()

                if self._check_button(BTN_STILL, now):
                    self._capture_still()

                self._check_recording_alive()
                time.sleep(POLL_S)

        except KeyboardInterrupt:
            pass
        finally:
            self._stop_recording()
            self._led(False)
            lgpio.gpiochip_close(self.h)


if __name__ == "__main__":
    Recorder().run()
