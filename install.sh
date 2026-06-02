#!/usr/bin/env bash
set -e

# Quip Wallet + Block Explorer — Installer for Linux VPS
# Usage: sudo bash install.sh [domain]
#   Or: curl -sSL https://raw.githubusercontent.com/JOSEonSOLANA/quip-explorer/main/install.sh | sudo bash -s [domain]

REPO_URL="https://github.com/JOSEonSOLANA/quip-explorer.git"
EXPLORER_DIR="/opt/quip-explorer"
EXPLORER_USER="quip"
DOMAIN="${1:-}"  # optional: pass domain for auto HTTPS

echo "==> Quip Explorer Installer"
echo ""

# --- Prepare system ---
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)."
  exit 1
fi

apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl

# --- Create user ---
if ! id -u "$EXPLORER_USER" &>/dev/null; then
  useradd -m -s /bin/bash "$EXPLORER_USER"
fi

# --- Clone / update code ---
if [ -d "$EXPLORER_DIR" ]; then
  echo "Updating existing installation..."
  cd "$EXPLORER_DIR"
  git pull
else
  echo "Cloning repository..."
  git clone --depth 1 "$REPO_URL" "$EXPLORER_DIR"
fi

cd "$EXPLORER_DIR"

# --- Python virtualenv ---
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
pip install -q gunicorn

# --- Create .env ---
cat > .env << 'EOF'
QUIP_VALIDATOR_URL=http://localhost:20049/rpc
PORT=8081
CACHE_DIR=/tmp/quip-cache
SCAN_DEPTH=50
EOF

chown -R "$EXPLORER_USER":"$EXPLORER_USER" "$EXPLORER_DIR"

# --- Systemd service ---
cat > /etc/systemd/system/quip-explorer.service << 'SERVICE'
[Unit]
Description=Quip Explorer (Wallet + Block Explorer)
After=network.target

[Service]
Type=simple
User=quip
WorkingDirectory=/opt/quip-explorer
EnvironmentFile=/opt/quip-explorer/.env
ExecStart=/opt/quip-explorer/venv/bin/gunicorn app:app -b 127.0.0.1:8081 --workers 2 --timeout 60
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable quip-explorer
systemctl start quip-explorer

echo "==> Explorer running on http://127.0.0.1:8081"

# --- Optional: Caddy reverse proxy ---
if [ -n "$DOMAIN" ]; then
  echo "Setting up Caddy with domain $DOMAIN..."

  if ! command -v caddy &>/dev/null; then
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy
  fi

  cat > /etc/caddy/Caddyfile << CADDY
$DOMAIN {
    reverse_proxy 127.0.0.1:8081
}
CADDY

  systemctl enable caddy
  systemctl restart caddy
  echo "==> HTTPS available at https://$DOMAIN"
fi

echo ""
echo "Done! Explorer is running."
echo "  Local:  http://localhost:8081"
if [ -n "$DOMAIN" ]; then
  echo "  Public: https://$DOMAIN"
fi
echo ""
echo "Logs: journalctl -u quip-explorer -f"
