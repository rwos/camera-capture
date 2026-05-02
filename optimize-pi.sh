#!/bin/bash
# Disables unnecessary services for headless appliance use.
# Safe to run multiple times.
set -euo pipefail

off() {
    for unit in "$@"; do
        if systemctl list-unit-files --no-legend "$unit" 2>/dev/null | grep -q .; then
            sudo systemctl disable --now "$unit" 2>/dev/null || true
            printf '  disabled %s\n' "$unit"
        fi
    done
}

echo "=== Tier 1: high savings ==="
off ModemManager.service
off cups.service cups-browsed.service cups.path cups.socket
off NetworkManager-wait-online.service
off bluetooth.service hciuart.service
off apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service

echo "=== Tier 2: desktop/GUI ==="
off lightdm.service
off glamor-test.service rp1-test.service
off wayvnc-control.service wayvnc.service
off accounts-daemon.service colord.service udisks2.service
off rpi-display-backlight.service

echo "=== Tier 3: small gains ==="
off triggerhappy.service triggerhappy.socket
off keyboard-setup.service console-setup.service
off cron.service
off apparmor.service
off dphys-swapfile.service

echo "=== config.txt ==="
if ! grep -qxF 'dtoverlay=disable-bt' /boot/firmware/config.txt; then
    sudo bash -c 'echo dtoverlay=disable-bt >> /boot/firmware/config.txt'
    echo "  added dtoverlay=disable-bt"
else
    echo "  dtoverlay=disable-bt already present"
fi

echo "=== Done — reboot to measure ==="
