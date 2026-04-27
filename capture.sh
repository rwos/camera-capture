#!/bin/bash
# Capture analog video to MJPEG AVI using hardware JPEG encoder.
# The Pi Zero 2W cannot do real-time UYVY->YUV420 conversion needed for H264,
# so we use v4l2jpegenc which accepts UYVY directly (no color conversion).
# Transcode to H264 on the Mac with: make transcode FILE=<basename>
#
# Frame rate note: with I2P deinterlacing enabled in setup-adv.py, the ADV7280
# outputs one progressive frame per analog field rather than per frame-pair,
# doubling the output rate: PAL 25fps source -> 50fps capture, NTSC ~30fps -> ~60fps.
# The framerate below must match or the AVI header will be wrong (wrong playback speed).
# To capture at native 25fps (PAL) / ~30fps (NTSC), disable I2P in setup-adv.py and
# change the framerate here to 25/1 (PAL) or 30000/1001 (NTSC). Output will be
# interlaced and require software deinterlacing in post.
filename=${1:-capture}

exec gst-launch-1.0 -e \
    v4l2src device=/dev/video0 ! \
    "video/x-raw,format=UYVY,width=720,height=576,framerate=50/1" ! \
    v4l2jpegenc ! \
    avimux ! \
    filesink location="/home/pi/${filename}.avi"
