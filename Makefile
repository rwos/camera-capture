PI_HOST = camera.local
PI_USER = pi
PI_PASS = raspberry

SSH  = sshpass -p "$(PI_PASS)" ssh  $(PI_USER)@$(PI_HOST)
SCP  = sshpass -p "$(PI_PASS)" scp
RSYNC = sshpass -p "$(PI_PASS)" rsync

.PHONY: ssh upload setup initial-setup settings-update optimize stream recordings

ssh:
	$(SSH)

upload:
	@echo "Uploading scripts..."
	$(SCP) setup-adv.py setup-pi.sh install_patch.sh recorder.py recorder.service optimize-pi.sh $(PI_USER)@$(PI_HOST):/home/pi/

setup: upload
	@echo "Installing packages and setting up recorder service..."
	$(SSH) "bash /home/pi/setup-pi.sh && sudo systemctl restart recorder.service"

initial-setup: setup
	@echo "Patching /boot/firmware/config.txt..."
	$(SSH) "grep -qxF 'dtoverlay=adv728x-m,adv7280m=1' /boot/firmware/config.txt || sudo bash -c 'echo dtoverlay=adv728x-m,adv7280m=1 >> /boot/firmware/config.txt'"
	$(SSH) "grep -qxF 'gpu_mem=128' /boot/firmware/config.txt || sudo bash -c 'echo gpu_mem=128 >> /boot/firmware/config.txt'"
	@echo "Building and installing patched adv7180 kernel module (takes a few minutes)..."
	$(SSH) "sudo bash /home/pi/install_patch.sh"
	@echo "Rebooting Pi..."
	-$(SSH) "sudo reboot"

optimize: upload
	@echo "Disabling unnecessary services..."
	$(SSH) "bash /home/pi/optimize-pi.sh"

settings-update: upload
	$(SSH) "python3 /home/pi/setup-adv.py"

stream:
	$(SSH) "python3 /home/pi/recorder.py --stream" | ffplay -fflags nobuffer -flags low_delay -probesize 32 -analyzeduration 0 -f mjpeg -

recordings:
	mkdir -p recordings
	$(RSYNC) -ah --progress --ignore-existing $(PI_USER)@$(PI_HOST):/home/pi/recordings/ recordings/
	$(SSH) "find /home/pi/recordings/ -type f -name '*.avi' -delete"
	for f in recordings/*.avi; do \
		[ -f "$$f" ] || continue; \
		out="$${f%.avi}.mp4"; \
		slow="$${f%.avi}_slow.mp4"; \
		[ -f "$$out" ] || ffmpeg -i "$$f" -c:v libx265 -crf 16 -preset fast -tag:v hvc1 -movflags +faststart "$$out"; \
		[ -f "$$slow" ] || ffmpeg -i "$$f" -vf "setpts=2.0*PTS" -r 25 -c:v libx265 -crf 16 -preset fast -tag:v hvc1 -movflags +faststart "$$slow"; \
	done

