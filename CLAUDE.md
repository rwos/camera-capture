# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Raspberry Pi video capture system for analog video (CVBS/S-Video) using the DVX-Nebula PCB, which is based on the [sdtv2csi](https://github.com/rniwase/sdtv2csi) design. The PCB uses an ADV7280 video decoder chip connected to the Pi via CSI. Primary use case is capturing/streaming from a Panasonic A2 camera (PAL composite).

Target hardware: Raspberry Pi Zero 2 W running Raspberry Pi OS Bookworm (Debian 12). The Pi is accessed over the network at `camera.local` (user: `pi`, password: `raspberry`).

## Makefile Commands

All commands run against the remote Pi via SSH/SCP using `sshpass`:

```bash
make ssh                    # Open SSH shell on the Pi
make upload                 # Deploy scripts to /home/pi/
make setup                  # Full Pi setup: install packages, CPU governor, configure ADV7280
make stream                 # Live MJPEG preview via ffplay (low latency, for debugging)
make down [FILE=basename]   # Download captured AVI from Pi (default: capture.avi)
make transcode [FILE=basename]  # Convert AVI -> H264 MP4 on Mac (default: capture)
```

Capture workflow:
1. `make setup` (first time / after reboot to reconfigure ADV7280)
2. SSH to Pi and run: `bash capture.sh <basename>` (records to `/home/pi/<basename>.avi`)
3. `make down FILE=<basename>` to download
4. `make transcode FILE=<basename>` to convert to H264 MP4

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
   Must be run after each Pi boot.

2. `capture.sh` -- Runs on the Pi. Records UYVY video using GStreamer + hardware JPEG encoder
   (`v4l2jpegenc`) to an MJPEG AVI file. No color conversion, no frame drops. Usage:
   `bash capture.sh <basename>` -> saves `/home/pi/<basename>.avi`.

3. `recorder.py` -- Appliance-mode daemon (run by `recorder.service`). Polls two GPIO buttons:
   Button 1 starts recording, Button 2 stops. Blinks the ACT LED while recording.

4. `setup-pi.sh` -- One-time Pi setup. Installs `gstreamer1.0-tools`, creates and enables
   a systemd service for persistent CPU performance mode, then runs `setup-adv.py`.

**Mac-side:**

5. `stills-from-video <rate 1-50> <avi-file>` -- Extracts JPEG frames from an AVI at the
   given rate (50 = every frame, 1 = one per second). Creates a directory named after the file.

### Key Video Dimensions

- V4L2 output: `720x576` @ **50fps** (PAL), UYVY422; NTSC: `720x507` @ **~60fps**
- I2P deinterlacing doubles the frame rate: one progressive frame per field, not per field-pair
  (PAL source 25fps → 50fps output; NTSC ~30fps → ~60fps output)
- GStreamer caps and AVI/MP4 container headers must use the doubled rate (50/1 for PAL)
- To capture at native 25fps (PAL) / ~30fps (NTSC): set ENABLE_I2P=False in setup-adv.py
  and update framerate in capture.sh and recorder.py; output will be interlaced
- Blanking: PAL active picture is full 720x576; vertical framing may have a ~20-row offset
  (bottom rows appearing at top) -- this is a known ADV7280 config issue, not yet resolved

### Mac-side Transcode

After `make down`, convert MJPEG AVI to H264 MP4 with `make transcode`:
```bash
ffmpeg -i capture.avi -c:v libx264 -preset fast -crf 20 -movflags +faststart capture.mp4
```
Mac handles this trivially fast (Apple Silicon). File sizes: MJPEG ~1.1MB/s (~66MB/min),
H264 ~0.5MB/s depending on scene complexity.

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
# install_patch.sh is included in this repo; upload it first with: make upload
sudo bash install_patch.sh
sudo bash -c 'echo "dtoverlay=adv728x-m,adv7280m=1" >> /boot/firmware/config.txt'
sudo bash -c 'echo "gpu_mem=128" >> /boot/firmware/config.txt'
sudo reboot
# After reboot:
make setup
```
