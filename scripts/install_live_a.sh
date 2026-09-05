#!/usr/bin/env bash
set -euo pipefail

# Install Linux Chrome + Xvfb + tesseract and the live Strategy A systemd unit.
# Does not restart or modify wallex-gold.service (paper).

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_USER="${SUDO_USER:-root}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

apt-get update -y
apt-get install -y xvfb tesseract-ocr fonts-liberation unzip wget

if ! command -v google-chrome >/dev/null 2>&1 && ! command -v chromium >/dev/null 2>&1; then
  wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  apt-get install -y /tmp/chrome.deb || apt-get install -y chromium-browser || apt-get install -y chromium
fi

cd "$APP_DIR"
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -r requirements.txt

unit_src="$APP_DIR/deploy/systemd/karamad-live-a.service.template"
unit_dst=/etc/systemd/system/karamad-live-a.service
sed \
  -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
  -e "s|__SERVICE_GROUP__|$SERVICE_GROUP|g" \
  -e "s|__APP_DIR__|$APP_DIR|g" \
  "$unit_src" > "$unit_dst"

systemctl daemon-reload
systemctl enable karamad-live-a.service
echo "Installed karamad-live-a.service. Paper bot was not restarted."
echo "Set KARAMAD_* in $APP_DIR/.env then: systemctl start karamad-live-a"
