# Quip Explorer

Wallet + Block Explorer for [Quip Testnet](https://quip.network). Browse balances, transaction history, blocks, extrinsics, and events.

## Features

- **Wallet Explorer** — search SS58 addresses, view balance (free/reserved/total), scan recent block activity
- **Block Explorer** — browse blocks, view extrinsics with decoded parameters, inspect events
- **Live balances** — always-up-to-date via RPC queries

## Quick Start

### Requirements

- Python 3.9+
- Access to a Quip node RPC (default: `http://localhost:20049/rpc`)

### Local

```bash
git clone https://github.com/JOSEonSOLANA/quip-explorer.git
cd quip-explorer
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn  # optional, for production

# Set your validator URL (optional, default: http://localhost:20049/rpc)
export QUIP_VALIDATOR_URL=http://localhost:20049/rpc

python app.py
# Open http://localhost:8081
```

### Production (Linux VPS)

**Option 1 — Automated script:**
```bash
sudo bash install.sh https://explorer.yourdomain.com
```

**Option 2 — Manual step by step:**
```bash
# 1. Clone
cd /opt
git clone https://github.com/JOSEonSOLANA/quip-explorer.git
cd quip-explorer

# 2. Create virtual environment (requires python3-venv)
apt install python3-venv -y
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
pip install gunicorn

# 4. Environment variables
cat > .env << 'EOF'
QUIP_VALIDATOR_URL=http://localhost:20049/rpc
PORT=8081
CACHE_DIR=/tmp/quip-cache
SCAN_DEPTH=50
EOF

# 5. Test (Ctrl+C to stop)
gunicorn app:app -b 0.0.0.0:8081 --workers 1 --timeout 120

# 6. (Optional) Systemd service for auto-start
cat > /etc/systemd/system/quip-explorer.service << 'SERVICE'
[Unit]
Description=Quip Explorer
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/quip-explorer
EnvironmentFile=/opt/quip-explorer/.env
ExecStart=/opt/quip-explorer/venv/bin/gunicorn app:app -b 0.0.0.0:8081 --workers 1 --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now quip-explorer
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `QUIP_VALIDATOR_URL` | `http://localhost:20049/rpc` | Substrate RPC endpoint |
| `PORT` | `8081` | Web server port |
| `CACHE_DIR` | `/tmp/cache` | Scan cache directory |
| `SCAN_DEPTH` | `50` | Number of recent blocks scanned for wallet events |

## API Endpoints

| Route | Description |
|---|---|
| `GET /` | Web UI |
| `GET /api/wallet/<ss58>` | Wallet balance + scan trigger |
| `GET /api/explorer/status` | Chain info (latest block, name) |
| `GET /api/explorer/blocks?from=N&limit=M` | Block list |
| `GET /api/explorer/block/<num>` | Block detail with extrinsics + events |
| `GET /api/explorer/search?q=<query>` | Search (block number, address, hash) |

## License

MIT
