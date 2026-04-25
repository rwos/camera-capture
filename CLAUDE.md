# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Raspberry Pi video capture system for analog video (CVBS/S-Video) using the DVX-Nebula PCB, which is based on the [sdtv2csi](https://github.com/rniwase/sdtv2csi) design. The PCB uses an ADV7280 video decoder chip connected to the Pi via CSI. Primary use case is capturing/streaming from a Panasonic AG-DVX100 camera.

Target hardware: Raspberry Pi Zero, 3, or 4 running Raspberry Pi OS Bookworm (Debian 12). The Pi is accessed over the network at `camera.local` (user: `pi`, password: `raspberry`).

## Makefile Commands

All commands run against the remote Pi via SSH/SCP using `sshpass`:

```bash
make ssh       # Open SSH shell on the Pi
make upload    # Deploy capture.sh and setup-adv.lua to /home/pi/
make setup     # Run setup-adv.lua on the Pi (configures ADV7280 chip)
make stream    # Stream live video from Pi and play with mplayer
make down      # Download test.mp4 from the Pi
```

## Architecture

### Video Pipeline

```
Analog video → ADV7280 (I2C config) → CSI → /dev/video0 (V4L2) → FFmpeg
```

1. `setup-adv.lua` — Runs on the Pi. Configures the ADV7280 chip over I2C (`/dev/i2c-10`, device address `0x21`). Sets input to composite (COMP), PAL mode, enables I2P de-interlacing (via VPP at address `0x42`), and enables automatic gain control.

2. `capture.sh` — Runs on the Pi. Records from `/dev/video0` using FFmpeg with hardware H.264 encoding (`h264_v4l2m2m`). Captures at 720x507 (includes blanking lines), burns in a timestamp overlay, outputs to `<filename>.mp4`. Usage: `bash capture.sh <basename>`.

3. `index.html` — Browser-based viewer for a live OGG/Theora stream served by the Pi at `camera.local:8080`.

### Key Video Dimensions

- Raw capture: `720x507` (includes vertical blanking lines)
- Active picture: `720x480` (NTSC) — crop with `crop=720:480:0:27`
- Configured mode in `setup-adv.lua`: PAL, progressive (I2P deinterlace ON)

### Kernel Module

`install_patch.sh` — Run once on the Pi as root. Builds and installs a patched `adv7180.ko` kernel module from source, matched to the running firmware version. Supports Bullseye and Bookworm. The patch extends the captured height from 480 to 507 lines to include blanking.

Required Pi config (`/boot/firmware/config.txt`):
```
dtoverlay=adv728x-m,adv7280m=1
gpu_mem=128
```

### `dvx-nebula/` Submodule

Contains DVX100 camera resources: Andromeda mod images, camera manual, and the original `capture.sh` / `setup-adv.lua` scripts from the sdtv2csi project. Not part of the active capture workflow.

## Pi Setup (one-time)

```bash
sudo apt install ffmpeg luarocks
sudo luarocks install bit32
sudo luarocks install lua-periphery
wget https://github.com/rniwase/sdtv2csi/raw/master/rpi/install_patch.sh
sudo bash install_patch.sh
sudo bash -c 'echo "dtoverlay=adv728x-m,adv7280m=1" >> /boot/firmware/config.txt'
sudo bash -c 'echo "gpu_mem=128" >> /boot/firmware/config.txt'
sudo reboot
```
