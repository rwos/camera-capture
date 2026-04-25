#!/bin/bash
# Capture analog video to MJPEG AVI using hardware JPEG encoder.
# The Pi Zero 2W cannot do real-time UYVY->YUV420 conversion needed for H264,
# so we use v4l2jpegenc which accepts UYVY directly (no color conversion).
# Transcode to H264 on the Mac with: make transcode FILE=<basename>
filename=${1:-capture}

exec gst-launch-1.0 -e \
    v4l2src device=/dev/video0 ! \
    "video/x-raw,format=UYVY,width=720,height=576,framerate=25/1" ! \
    v4l2jpegenc ! \
    avimux ! \
    filesink location="/home/pi/${filename}.avi"
