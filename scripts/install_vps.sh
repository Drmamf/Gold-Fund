#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
SERVICE_GROUP="${SERVICE_GROUP:-$(id -gn "$SERVICE_USER")}" 
SERVICE_NAME="wallex-gold.service"

cd "$APP_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[STOP] .env created. Fill real PostgreSQL + Bale secrets, then rerun this script."
  exit 2
fi
if grep -q 'CHANGE_ME' .env; then
  echo "[STOP] .env still contains CHANGE_ME. Fill real secrets first."
  exit 2
fi

command -v docker >/dev/null || { echo '[ERROR] docker is required'; exit 1; }
command -v python3 >/dev/null || { echo '[ERROR] python3 is required'; exit 1; }

docker compose up -d db

python3 -m venv .venv
.venv/bin/pip install --upgrade pip wheel
.venv/bin/pip install -r requirements.txt

# Fresh DB create + idempotent seeds.
.venv/bin/python scripts/init_db.py
.venv/bin/python scripts/preflight.py

TMP_SERVICE="$(mktemp)"
sed \
  -e "s#__APP_DIR__#$APP_DIR#g" \
  -e "s#__SERVICE_USER__#$SERVICE_USER#g" \
  -e "s#__SERVICE_GROUP__#$SERVICE_GROUP#g" \
  deploy/systemd/wallex-gold.service.template > "$TMP_SERVICE"

sudo cp "$TMP_SERVICE" "/etc/systemd/system/$SERVICE_NAME"
rm -f "$TMP_SERVICE"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

echo "[OK] service installed and started"
echo "Status:  sudo systemctl status $SERVICE_NAME"
echo "Logs:    journalctl -u $SERVICE_NAME -f"
echo "App log: tail -f $APP_DIR/logs/wallex_gold_bot.log"
