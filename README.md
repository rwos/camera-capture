# Raspberry Pi Analog Video Capture

> **WARNING:** this is entirely vibe-coded. It works for me, but it may set your computer
> and camera on fire. Everything in here (especially comments) might be totally wrong.
> Use with some skepticism.

Capture analog video (composite CVBS or S-Video) at full quality on a Raspberry Pi Zero 2W,
using the [ADV7280 uHat Shield](https://hackaday.io/project/201713-analog-video-capture-adv7280-uhat-shield).

Available from [Ethermata on Tindie](https://www.tindie.com/products/ethermata/analog-video-capture-adv7280m-uhat-prototype/). Based on the [sdtv2csi](https://github.com/rniwase/sdtv2csi) project by rniwase.

---

## Hardware required

- Raspberry Pi Zero 2W
- ADV7280 uHat Shield ([Hackaday.io](https://hackaday.io/project/201713-analog-video-capture-adv7280-uhat-shield))
- SD card (Class 10 / A1 recommended -- captures run ~1.1 MB/s)
- Analog video source (composite CVBS or S-Video)

The hat connects via the 40-pin GPIO header and routes video through the CSI ribbon connector.

---

## How it works

```
Analog video → ADV7280 → CSI → V4L2 (/dev/video0) → GStreamer → MJPEG AVI
```

The ADV7280 chip converts the analog signal to a digital UYVY stream over MIPI CSI-2. A patched `adv7180` kernel module tells the driver the correct frame dimensions.

GStreamer captures from V4L2 and encodes using the Pi's **hardware JPEG encoder** (`v4l2jpegenc`), which accepts UYVY directly — no CPU-side colour conversion. This matters: the Pi Zero 2W can't do real-time H.264 because that requires a CPU-side colour space conversion that maxes out at ~23 fps on this hardware, well below the 50 fps input rate.

With I2P deinterlacing enabled (the default), the ADV7280 outputs one progressive frame per *field* rather than per frame-pair, doubling the output rate: **50 fps for PAL, ~60 fps for NTSC**. Captures are saved as MJPEG AVI (~1.1 MB/s). `make recordings` syncs them to the Mac and transcodes to H.265.

---

## Camera-specific settings

**These are the only values you need to change for a different camera:**

In `setup-adv.py`, near the top:

```python
STANDARD = "PAL"   # "PAL" for European/50Hz cameras, "NTSC" for USA/Japan/60Hz
```

And in `recorder.py`, the GStreamer caps line:

```bash
"video/x-raw,format=UYVY,width=720,height=576,framerate=50/1"
#                                        ^^^              ^^
#                         PAL=576, NTSC=507*    with I2P: PAL=50, NTSC=60000/1001 (~60)
#                                               no I2P:   PAL=25/1, NTSC=30000/1001
# * NTSC=507 requires the patched adv7180 kernel module (install_patch.sh)
```

The framerate is doubled by I2P deinterlacing (one frame per field). If you disable
`ENABLE_I2P` in `setup-adv.py`, change the framerate here to match the native source
rate: `25/1` for PAL, `30000/1001` for NTSC.

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

## Capturing video

### Appliance mode (normal use)

After `make setup` the Pi runs `recorder.service` automatically on every boot.
Wait for the ACT LED to do 3 quick blinks -- that means the recorder is ready.
While idle the LED pulses briefly every 2 seconds.

| Button | Idle | Recording |
|--------|------|-----------|
| Button 1 (GPIO 24, corner) | Start recording | Nothing |
| Button 2 (GPIO 23) | Nothing | Stop recording |

Recordings are saved to `/home/pi/recordings/` with timestamped names
(`video_YYYYMMDD_HHMMSS.avi`).

To sync to the Mac, transcode to H.265, and delete from the Pi:

```bash
make recordings
# -> recordings/video_YYYYMMDD_HHMMSS.mp4      (H.265, CRF 16, normal speed)
# -> recordings/video_YYYYMMDD_HHMMSS_slow.mp4 (H.265, CRF 16, half speed)
```

### Extracting stills from a recording

`stills-from-video` (included) extracts JPEG frames from an AVI at a chosen rate.
Creates a directory named after the file; frames saved as `frame_000001.jpg`, etc.
Maximum JPEG quality (`-q:v 1`). Runs on Mac/PC; requires `ffmpeg`.

```bash
stills-from-video 1  video_20261015_143022.avi   # one frame per second
stills-from-video 10 video_20261015_143022.avi   # 10 frames per second
stills-from-video 50 video_20261015_143022.avi   # every frame (50fps source)
# -> video_20261015_143022/frame_000001.jpg ...
```

---

## Live preview (for setup / debugging)

Streams MJPEG from the Pi and plays it locally via ffplay:

```bash
make stream
```

Low latency (~1 frame). Useful for checking framing and signal lock before capturing.
