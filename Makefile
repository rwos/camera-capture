FILE ?= capture
STREAM_CMD=gst-launch-1.0 -q -e v4l2src device=/dev/video0 ! 'video/x-raw,format=UYVY,width=720,height=576,framerate=25/1' ! v4l2jpegenc ! fdsink fd=1

ssh:
	sshpass -p "raspberry" ssh pi@camera.local

upload:
	sshpass -p "raspberry" scp capture.sh setup-adv.py setup-pi.sh install_patch.sh recorder.py recorder.service pi@camera.local:/home/pi/

setup: upload
	sshpass -p "raspberry" ssh pi@camera.local "bash /home/pi/setup-pi.sh"

stream:
	sshpass -p "raspberry" ssh pi@camera.local "$(STREAM_CMD)" | ffplay -fflags nobuffer -flags low_delay -probesize 32 -analyzeduration 0 -f mjpeg -

down:
	sshpass -p "raspberry" scp pi@camera.local:/home/pi/$(FILE).avi ./$(FILE).avi

recordings:
	mkdir -p recordings
	sshpass -p "raspberry" scp -r pi@camera.local:/home/pi/recordings/. recordings/

transcode:
	ffmpeg -i $(FILE).avi -c:v libx264 -preset fast -crf 20 -movflags +faststart $(FILE).mp4
