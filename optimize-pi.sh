#!/bin/bash
# Boot time optimizations for headless appliance use. Safe to re-run.
# Run via: make optimize
set -euo pipefail

off() {
    for unit in "$@"; do
        state=$(systemctl is-enabled "$unit" 2>/dev/null || true)
        case "$state" in
            enabled|enabled-runtime)
                sudo systemctl disable --now "$unit" 2>/dev/null || true
                ;;
            static|indirect)
                sudo systemctl mask --now "$unit" 2>/dev/null || true
                ;;
        esac
    done
}

off ModemManager.service
off cups.service cups-browsed.service cups.path cups.socket
off NetworkManager-wait-online.service
off bluetooth.service hciuart.service
off apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service

off lightdm.service
off glamor-test.service rp1-test.service
off wayvnc-control.service wayvnc.service
off accounts-daemon.service colord.service udisks2.service
off rpi-display-backlight.service

off triggerhappy.service triggerhappy.socket
off keyboard-setup.service console-setup.service
off cron.service
off apparmor.service
off dphys-swapfile.service
off rpi-eeprom-update.service
off rpc-statd-notify.service
off alsa-restore.service alsa-state.service
off rtkit-daemon.service
off systemd-rfkill.service systemd-rfkill.socket

off plymouth-start.service plymouth-read-write.service plymouth-quit.service plymouth-quit-wait.service
sudo sed -i 's/ splash//' /boot/firmware/cmdline.txt 2>/dev/null || true

sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/volatile.conf > /dev/null << 'EOF'
[Journal]
Storage=volatile
EOF

grep -qxF 'dtoverlay=disable-bt' /boot/firmware/config.txt \
    || sudo bash -c 'echo dtoverlay=disable-bt >> /boot/firmware/config.txt'

sudo cp /home/pi/recorder.service /etc/systemd/system/recorder.service
sudo systemctl daemon-reload
