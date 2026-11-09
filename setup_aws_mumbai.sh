#!/bin/bash
# =============================================================================
# Automated AWS Mumbai (ap-south-1) 24/7 Deployment Script for Vijay Sai's Dhan Algo
# =============================================================================

set -e

echo "================================================================="
echo " Setting up Vijay Sai's Dhan Algo on AWS Mumbai EC2 (ap-south-1) "
echo "================================================================="

# Update system
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install -y python3-pip python3-venv git curl htop ufw

# Create app directory if not already inside
APP_DIR="/home/ubuntu/dhan-nifty-algo"

# Setup Python virtual environment
cd "$APP_DIR"
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create systemd service for 24/7 autonomous background running
echo "[*] Configuring 24/7 systemd background service..."
sudo bash -c "cat > /etc/systemd/system/dhan-algo.service << 'EOF'
[Unit]
Description=Vijay Sai's Dhan Algo Trading Engine (24/7 Daemon)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/dhan-nifty-algo
ExecStart=/home/ubuntu/dhan-nifty-algo/.venv/bin/python app.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
Environment=PORT=5000

[Install]
WantedBy=multi-user.target
EOF"

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable dhan-algo.service
sudo systemctl restart dhan-algo.service

# Configure Firewall
sudo ufw allow 5000/tcp
sudo ufw allow 22/tcp
sudo ufw --force enable

echo "================================================================="
echo " [SUCCESS] 24/7 Trading Engine is LIVE on AWS Mumbai EC2!"
echo " Check status anytime: sudo systemctl status dhan-algo.service"
echo " View live logs:       journalctl -u dhan-algo.service -f"
echo "================================================================="
