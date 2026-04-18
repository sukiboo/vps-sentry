#!/usr/bin/env bash
# Idempotent installer for vps-sentry. Run as root on the target VPS.
#   sudo ./deploy/install.sh /path/to/checkout
set -euo pipefail

SRC="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
DEST="/opt/vps-monitor"
UNIT="/etc/systemd/system/vps-monitor.service"
SERVICE_USER="monitor"

if [[ $EUID -ne 0 ]]; then
    echo "install.sh must be run as root" >&2
    exit 1
fi

if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

mkdir -p "$DEST"
# Sync source, excluding venv/.git; keep .env and config.yml if they already exist on the host.
rsync -a --delete \
    --exclude='.venv' --exclude='.git' --exclude='__pycache__' \
    --exclude='.env' --exclude='config.yml' \
    "$SRC/" "$DEST/"

# Seed config/.env only if missing on the host.
[[ -f "$DEST/.env" ]]        || cp "$SRC/.env"        "$DEST/.env"        2>/dev/null || true
[[ -f "$DEST/config.yml" ]]  || cp "$SRC/config.example.yml" "$DEST/config.yml"

if [[ ! -d "$DEST/.venv" ]]; then
    python3 -m venv "$DEST/.venv"
fi
"$DEST/.venv/bin/pip" install --quiet --upgrade pip
"$DEST/.venv/bin/pip" install --quiet -r "$DEST/requirements.txt"

mkdir -p "$DEST/logs"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DEST"
chmod 600 "$DEST/.env"

cp "$DEST/deploy/vps-monitor.service" "$UNIT"
systemctl daemon-reload
systemctl enable --now vps-monitor.service

echo "vps-sentry installed. Tail logs with: journalctl -u vps-monitor -f"
