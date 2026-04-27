.PHONY: ssh upload initial-setup stream recordings

ssh:
	sshpass -p "raspberry" ssh pi@camera.local

upload:
	@echo "Uploading scripts..."
	sshpass -p "raspberry" scp setup-adv.py setup-pi.sh install_patch.sh recorder.py recorder.service pi@camera.local:/home/pi/

initial-setup: upload
	@echo "Patching /boot/firmware/config.txt..."
	sshpass -p "raspberry" ssh pi@camera.local "grep -qxF 'dtoverlay=adv728x-m,adv7280m=1' /boot/firmware/config.txt || sudo bash -c 'echo dtoverlay=adv728x-m,adv7280m=1 >> /boot/firmware/config.txt'"
	sshpass -p "raspberry" ssh pi@camera.local "grep -qxF 'gpu_mem=128' /boot/firmware/config.txt || sudo bash -c 'echo gpu_mem=128 >> /boot/firmware/config.txt'"
	@echo "Building and installing patched adv7180 kernel module (takes a few minutes)..."
	sshpass -p "raspberry" ssh pi@camera.local "sudo bash /home/pi/install_patch.sh"
	@echo "Installing packages and setting up recorder service..."
	sshpass -p "raspberry" ssh pi@camera.local "bash /home/pi/setup-pi.sh"
	@echo "Rebooting Pi..."
	-sshpass -p "raspberry" ssh pi@camera.local "sudo reboot"

stream:
	sshpass -p "raspberry" ssh pi@camera.local "python3 /home/pi/recorder.py --stream" | ffplay -fflags nobuffer -flags low_delay -probesize 32 -analyzeduration 0 -f mjpeg -

recordings:
	mkdir -p recordings
	sshpass -p "raspberry" rsync -ah --progress --ignore-existing pi@camera.local:/home/pi/recordings/ recordings/
	sshpass -p "raspberry" ssh pi@camera.local "find /home/pi/recordings/ -type f -name '*.avi' -delete"
	for f in recordings/*.avi; do \
		[ -f "$$f" ] || continue; \
		out="$${f%.avi}.mp4"; \
		slow="$${f%.avi}_slow.mp4"; \
		[ -f "$$out" ] || ffmpeg -i "$$f" -c:v libx265 -crf 16 -preset fast -tag:v hvc1 -movflags +faststart "$$out"; \
		[ -f "$$slow" ] || ffmpeg -i "$$f" -vf "setpts=2.0*PTS" -r 25 -c:v libx265 -crf 16 -preset fast -tag:v hvc1 -movflags +faststart "$$slow"; \
	done

