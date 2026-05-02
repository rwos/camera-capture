# Raspberry Pi Analog Video Capture

> **WARNING:** this is entirely vibe-coded. It works for me, but it may set your computer
> and camera on fire. Everything in here (especially comments) might be totally wrong.
> Use with some skepticism.

Capture analog video (composite CVBS or S-Video) on a Raspberry Pi Zero 2W using the
[ADV7280 uHat Shield](https://hackaday.io/project/201713-analog-video-capture-adv7280-uhat-shield).
Configured for PAL by default; NTSC is supported with minor changes (see below).

* Hardware (was) available from [Ethermata on Tindie](https://www.tindie.com/products/ethermata/analog-video-capture-adv7280m-uhat-prototype/).
* Hardware creator's repo: [lerabot/dvx-nebula](https://github.com/lerabot/dvx-nebula).
* Based on [sdtv2csi](https://github.com/rniwase/sdtv2csi) by rniwase.

This does ***not*** capture sound. I don't remember if the hardware would
support that, I think it would have to go through the rPi directly. I capture
sound separately, so there's nothing in here that does sound.

## Hardware required

- Raspberry Pi Zero 2W
- ADV7280 uHat Shield ([Hackaday.io](https://hackaday.io/project/201713-analog-video-capture-adv7280-uhat-shield))
- SD card (Class 10 / A1 recommended -- captures run ~1.1 MB/s)
- Analog video source (composite CVBS or S-Video)
- (optional) push button, LED + resistor

The hat connects via the 40-pin GPIO header and routes video through the CSI ribbon connector.

## How it works

```
Analog video → ADV7280 → CSI → V4L2 (/dev/video0) → GStreamer → MJPEG AVI
```

The ADV7280 converts the analog signal to a UYVY stream over MIPI CSI-2. A patched `adv7180`
kernel module sets the correct frame dimensions.

GStreamer encodes using the Pi's **hardware JPEG encoder** (`v4l2jpegenc`), which accepts UYVY
directly with no CPU-side colour conversion. The Pi Zero 2W cannot do real-time H.264 from this
source: the required UYVY→YUV420p conversion maxes out at ~23 fps, below the 50 fps input rate.

With I2P deinterlacing (the default), the ADV7280 outputs one progressive frame per field,
doubling the rate: **50 fps for PAL, ~60 fps for NTSC**. Captures are saved as MJPEG AVI
(~1.1 MB/s). `make recordings` syncs to the Mac and transcodes to H.265.

## Camera-specific settings

`setup-adv.py` has configurable options near the top: input source (CVBS/S-Video), signal
standard, I2P deinterlacing, AGC, contrast, and adaptive contrast. After changing settings:

```bash
make settings-update
```

### Switching to NTSC

In `setup-adv.py`:

```python
STANDARD = "NTSC"
```

In `recorder.py`:

```python
GST_CAPS      = "video/x-raw,format=UYVY,width=720,height=507"
GST_FRAMERATE = "60000/1001"   # NTSC + I2P (~59.94 fps)
```

(With I2P disabled: `GST_FRAMERATE = "30000/1001"`.) `make stream` uses the same settings.

---

## Initial setup (once)

1. Flash **Raspberry Pi OS Bookworm (64-bit Lite)** with [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Set hostname to `camera`, enable SSH, configure Wi-Fi.
2. Boot the Pi, then from your Mac/PC (requires `sshpass` and `ffmpeg` -- `brew install sshpass ffmpeg`):

```bash
make initial-setup
```

Uploads scripts, installs packages, patches `config.txt`, builds and installs the patched
`adv7180` kernel module (takes a few minutes), and reboots. The recorder service starts
automatically on every subsequent boot.

To re-apply after changing `recorder.py` or other scripts (without rebuilding the kernel module):

```bash
make setup
```

## Capturing video

### Appliance mode (normal use)

The Pi runs `recorder.service` on every boot. The LED does 3 quick blinks when
the recorder is ready, then a double-blink every 2.5 seconds while idle. Press
the button to start or stop recording.

Recordings are saved to `/home/pi/recordings/` as `video_YYYYMMDD_HHMMSS.avi`.

To sync to the Mac, transcode to H.265, and delete from the Pi:

```bash
make recordings
# -> recordings/video_YYYYMMDD_HHMMSS.mp4      (H.265, CRF 16, normal speed)
# -> recordings/video_YYYYMMDD_HHMMSS_slow.mp4 (H.265, CRF 16, half speed)
```

#### Button and LED wiring

| Physical pin | GPIO | Connection |
|---|---|---|
| 38 | GPIO 20 | Button: one leg to pin 38, other leg to GND |
| 40 | GPIO 21 | LED anode (+) → 150Ω → GPIO 21; cathode (−) → GND |

GPIO assignments can be changed via `BTN_TOGGLE` and `LED_GPIO` at the top of `recorder.py`.

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

## Live preview (for setup / debugging)

```bash
make stream
```

Streams MJPEG from the Pi and plays it locally via ffplay.
