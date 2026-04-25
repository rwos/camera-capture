STREAM_CMD=ffmpeg -s 720x507 -vf video4linux2 -i /dev/video0 -f avi -
STREAM_CMD=ffmpeg -an -video_size 720x507 -r 29.97 -i /dev/video0 -f avi -
STREAM_CMD=ffmpeg -f v4l2 -video_size 720x480 -i /dev/video0 -c:v h264_v4l2m2m -pix_fmt yuv420p -vf "crop=720:480:0:0" -b:v 8M -f rawvideo -
STREAM_CMD=ffmpeg -f v4l2 -video_size 720x480 -i /dev/video0 -c:v h264_v4l2m2m -pix_fmt yuv420p -vf "crop=720:480:0:0" -b:v 8M -f avi -

ssh:
	sshpass -p "raspberry" ssh pi@camera.local

stream:
	sshpass -p "raspberry" ssh pi@camera.local "$(STREAM_CMD)" | mplayer -

upload:
	sshpass -p "raspberry" scp -r ./capture.sh ./setup-adv.py pi@camera.local:/home/pi/

setup:
	sshpass -p "raspberry" ssh pi@camera.local "python3 ./setup-adv.py"

down:
	sshpass -p "raspberry" scp -v -r pi@camera.local:/home/pi/test.mp4 ./
