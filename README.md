# Raspberry Pi Analog Video Capture

Capture analog video (composite CVBS or S-Video) at full quality on a Raspberry Pi Zero 2W,
using the [DVX-Nebula ADV7280 uHAT](https://hackaday.io/project/201713-analog-video-capture-adv7280-uhat-shield).

Available from [Ethermata on Tindie](https://www.tindie.com/products/ethermata/analog-video-capture-adv7280m-uhat-prototype/). Based on the [sdtv2csi](https://github.com/rniwase/sdtv2csi) project by rniwase.

---

## Hardware required

- Raspberry Pi Zero 2W
- DVX-Nebula ADV7280 uHAT ([Hackaday.io](https://hackaday.io/project/201713-analog-video-capture-adv7280-uhat-shield))
- SD card (Class 10 / A1 recommended -- captures run ~1.1 MB/s)
- Analog video source (composite CVBS or S-Video)

The hat connects via the 40-pin GPIO header and routes video through the CSI ribbon connector.

---

## How it works

```
Analog video -> ADV7280 decoder (I2C config) -> CSI -> /dev/video0 (V4L2, UYVY422) -> GStreamer -> MJPEG AVI
```

The ADV7280 converts analog video to a digital UYVY stream over MIPI CSI-2. A patched
`adv7180` kernel module tells the driver the correct frame dimensions. GStreamer captures
from V4L2 and encodes using the Pi's hardware JPEG encoder (`v4l2jpegenc`), which accepts
UYVY directly -- no CPU-side colour conversion needed. This is the only pipeline that
achieves full 25 fps (PAL) on the Pi Zero 2W.

Captures are saved as MJPEG AVI (~1.1 MB/s) to the Pi's SD card. `make recordings`
syncs them to the Mac and transcodes to H.265 MP4 -- Apple Silicon handles this in
well under real-time.

---

## Camera-specific settings

**These are the only values you need to change for a different camera:**

In `setup-adv.py`, near the top:

```python
STANDARD = "PAL"   # "PAL" for European/50Hz cameras, "NTSC" for USA/Japan/60Hz
```

And in `capture.sh`, the GStreamer caps line:

```bash
"video/x-raw,format=UYVY,width=720,height=576,framerate=25/1"
#                                        ^^^         ^^
#                         PAL=576, NTSC=507*         PAL=25, NTSC=30 (29.97)
# * NTSC=507 requires the patched adv7180 kernel module (install_patch.sh)
```

Input type (composite vs S-Video) is set by the device tree overlay and matches the
physical connector you plug into. See the DVX-Nebula documentation for wiring.

---

## Initial setup (once)

Do this once, from your Mac/PC:

### 1. Flash Raspberry Pi OS Bookworm (64-bit Lite) to the SD card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Configure SSH and
your network credentials in the imager before writing.

### 2. Boot the Pi and set config.txt

SSH in and add to `/boot/firmware/config.txt`:

```
dtoverlay=adv728x-m,adv7280m=1
gpu_mem=128
```

```bash
sudo bash -c 'echo "dtoverlay=adv728x-m,adv7280m=1" >> /boot/firmware/config.txt'
sudo bash -c 'echo "gpu_mem=128" >> /boot/firmware/config.txt'
```

### 3. Upload scripts to the Pi

From your Mac/PC (requires `sshpass` -- `brew install sshpass`; `make stream` also requires `ffplay` -- `brew install ffmpeg`):

```bash
make upload
```

### 4. Install the patched kernel module

The stock `adv7180` kernel driver reports the wrong frame height with I2P deinterlacing
enabled. `install_patch.sh` (included in this repo) builds and installs a corrected
version matched to your running kernel:

```bash
# SSH into the Pi first (make ssh), then:
sudo bash install_patch.sh
```

This takes several minutes -- it downloads the kernel source and compiles the module.

### 5. Reboot

```bash
sudo reboot
```

### 6. Run the main setup

```bash
make setup
```

This uploads scripts to the Pi, installs GStreamer and the recorder service, and sets
the CPU governor to `performance` mode (persistent across reboots). The Pi reboots
automatically; the recorder service then starts on boot and configures the ADV7280.

---

## Per-boot setup

`setup-adv.py` must run after every boot to configure the ADV7280 chip (the kernel
driver does not apply all required settings at probe time). The `recorder.service`
installed by `make setup` runs it automatically on boot via `ExecStartPre`. To run
it manually:

```bash
python3 setup-adv.py
```

---

## Capturing video

### Appliance mode (normal use)

After `make setup` the Pi runs `recorder.service` automatically on every boot.
Wait for the ACT LED to do 3 quick blinks -- that means the recorder is ready.
While idle the LED pulses briefly every 2 seconds.

| Button | Idle | Recording |
|--------|------|-----------|
| Button 1 (GPIO 24, corner) | Start recording | Nothing |
| Button 2 (GPIO 23) | Capture still | Stop recording |

Recordings and stills are saved to `/home/pi/recordings/` with timestamped names
(`video_YYYYMMDD_HHMMSS.avi`, `still_YYYYMMDD_HHMMSS.jpg`).

To sync to the Mac, transcode to H.265, and delete from the Pi:

```bash
make recordings
# -> recordings/video_YYYYMMDD_HHMMSS.mp4 (H.265, CRF 16)
```

### Manual capture (via SSH)

```bash
# SSH into the Pi:
make ssh
bash capture.sh my-clip
# -> saves /home/pi/my-clip.avi

# On Mac/PC: download and transcode
make down FILE=my-clip
make transcode FILE=my-clip
# -> my-clip.mp4 (H.264)
```

---

## Live preview (for setup / debugging)

Streams MJPEG from the Pi and plays it locally via ffplay:

```bash
make stream
```

Low latency (~1 frame). Useful for checking framing and signal lock before capturing.

---

## Makefile reference

All `make` targets run against the Pi over SSH (requires `sshpass` on the Mac/PC).
Default Pi address is `camera.local`; edit the Makefile if yours differs.

| Target | What it does |
|--------|-------------|
| `make ssh` | Open an interactive SSH shell on the Pi |
| `make upload` | Copy scripts to `/home/pi/` on the Pi |
| `make setup` | Upload + install packages + recorder service; reboots Pi |
| `make stream` | Live MJPEG preview via ffplay |
| `make recordings` | Sync `/home/pi/recordings/` to Mac, delete from Pi, transcode to H.265 |
| `make down [FILE=name]` | Download `name.avi` from Pi (default: `capture`) |
| `make transcode [FILE=name]` | Convert `name.avi` -> `name.mp4` (H.264) on Mac |

---

## Troubleshooting

**No signal / black screen**
- Run `python3 setup-adv.py` -- it prints signal lock status. Look for `LOCKED`.
- Check that `STANDARD` in `setup-adv.py` matches your camera (PAL vs NTSC).
- Verify the dtoverlay line is in `/boot/firmware/config.txt` and the Pi has rebooted since.

**Vertical framing offset (image rolls or bottom rows appear at top)**
- Known issue with the ADV7280 and I2P deinterlacing. See `setup-adv.py` comments for details.
- The kernel module patch (`install_patch.sh`) is required.

**Frame drops during capture**
- Ensure the CPU governor is set to `performance`: `cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`
- Re-run `make setup` to restore it.

**`make setup` fails with "sshpass not found"**
- `brew install sshpass` on Mac.
