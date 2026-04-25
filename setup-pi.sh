#!/bin/bash
# One-time Pi setup: installs packages, configures persistent CPU performance mode,
# and configures the ADV7280 chip. Safe to re-run.
set -e

echo "=== Installing packages ==="
sudo apt-get install -y gstreamer1.0-tools python3-lgpio ffmpeg

echo "=== Setting CPU governor to performance mode (persistent across reboots) ==="
sudo tee /etc/systemd/system/cpu-performance.service > /dev/null << 'EOF'
[Unit]
Description=Set CPU governor to performance mode
After=sysinit.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now cpu-performance
echo "CPU governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"

echo "=== Installing recorder service ==="
sudo cp /home/pi/recorder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable recorder

echo "=== Setup complete ==="
