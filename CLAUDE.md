# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Raspberry Pi video capture system for analog video (CVBS/S-Video) using the ADV7280 uHat Shield, which is based on the [sdtv2csi](https://github.com/rniwase/sdtv2csi) design. The PCB uses an ADV7280 video decoder chip connected to the Pi via CSI. Primary use case is capturing/streaming from a Panasonic A2 camera (PAL composite).

Target hardware: Raspberry Pi Zero 2 W running Raspberry Pi OS Bookworm (Debian 12). The Pi is accessed over the network at `camera.local` (user: `pi`, password: `raspberry`).

**Goal:** The system should set up from a blank Raspberry Pi OS image with as few dependencies as possible. Avoid pulling in extra packages, Python libraries, or tooling beyond what is strictly necessary.

## Makefile Commands

All commands run against the remote Pi via SSH/SCP using `sshpass`:

```bash
make ssh                    # Open SSH shell on the Pi
make upload                 # Deploy scripts to /home/pi/
make setup                  # Upload scripts and re-run setup-pi.sh (use after changing recorder.py etc.)
make initial-setup          # One-time Pi setup: setup + config.txt + kernel module + reboot
make settings-update        # Upload and apply setup-adv.py after changing settings
make stream                 # Live MJPEG preview via ffplay (low latency, for debugging)
make recordings             # Sync recordings from Pi, transcode to H.265, delete from Pi
```

Capture workflow:
1. `make initial-setup` (once, on a fresh Pi)
2. Use the hardware buttons to record; `make recordings` to sync and transcode

## Architecture

### Video Pipeline

```
Analog video -> ADV7280 (I2C config) -> CSI -> /dev/video0 (V4L2, UYVY422) -> GStreamer
```

The camera is PAL composite. The ADV7280 with I2P deinterlacing outputs:
- Format: UYVY 4:2:2 (packed)
- Resolution: 720x576 @ 50fps (PAL); 720x507 @ ~60fps (NTSC)

### Why GStreamer + MJPEG (not FFmpeg + H264)

The Pi Zero 2W cannot encode H264 in real-time from this source. The bottleneck is
software color space conversion: UYVY422 -> YUV420p (required by `h264_v4l2m2m`) maxes
out at ~23fps on a 1GHz Cortex-A53, well below the 50fps (PAL) input rate with I2P.

The hardware JPEG encoder (`v4l2jpegenc`) accepts UYVY directly, bypassing the color
conversion entirely, and keeps up with the 50fps (PAL) input rate in practice.

CPU governor is set to `performance` (1GHz) persistently via systemd -- this is handled
by `setup-pi.sh` / `make setup`.

### Scripts

**Pi-side:**

1. `setup-adv.py` -- Configures the ADV7280 chip over I2C (`/dev/i2c-10`, address `0x21`).
   Sets input to composite (COMP), PAL mode, enables I2P de-interlacing, enables AGC.
   Run automatically on boot by `recorder.service`.

2. `recorder.py` -- Appliance-mode daemon (run by `recorder.service`). Polls one GPIO button
   (GPIO24, corner): toggles recording. Blinks an external LED (GPIO25, active-high) while
   recording; double-blinks while idle.

3. `setup-pi.sh` -- One-time Pi setup. Installs `gstreamer1.0-tools`, creates and enables
   a systemd service for persistent CPU performance mode, then runs `setup-adv.py`.

**Mac-side:**

4. `stills-from-video <rate 1-50> <avi-file>` -- Extracts JPEG frames from an AVI at the
   given rate (50 = every frame, 1 = one per second). Creates a directory named after the file.

### Key Video Dimensions

- V4L2 output dimensions: `720x576` (PAL), `720x507` (NTSC), UYVY422
- V4L2 driver reports 25fps (PAL) / ~30fps (NTSC) via timeperframe (field rate)
- I2P deinterlacing doubles the actual frame rate: one progressive frame per field, not per
  field-pair (PAL: 50fps output; NTSC: ~60fps output)
- `capssetter` in the GStreamer pipeline overrides the framerate metadata downstream so the
  AVI header uses the correct doubled rate; `GST_FRAMERATE` in recorder.py controls this
- To capture at native 25fps (PAL) / ~30fps (NTSC): set ENABLE_I2P=False in setup-adv.py
  and update `GST_CAPS` (height) and `GST_FRAMERATE` in recorder.py; output will be interlaced

### Kernel Module

`install_patch.sh` -- Run once on the Pi as root. Builds and installs a patched `adv7180.ko`
kernel module from source, matched to the running firmware version. The patch extends
captured height to include blanking lines.

Required Pi config (`/boot/firmware/config.txt`):
```
dtoverlay=adv728x-m,adv7280m=1
gpu_mem=128
```

## Pi Setup (one-time)

```bash
make initial-setup
```

Uploads scripts, installs packages, patches `config.txt`, builds and installs the patched `adv7180` kernel module, and reboots.
