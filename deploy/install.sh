#!/usr/bin/env bash
# Idempotent installer for vps-sentry. Run as root on the target VPS.
#   sudo ./deploy/install.sh /path/to/checkout
set -euo pipefail

SRC="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
DEST="/opt/vps-sentry"
UNIT="/etc/systemd/system/vps-sentry.service"
SERVICE_USER="vps-sentry"

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
    --exclude='.env' --exclude='config.yml' --exclude='state' \
    "$SRC/" "$DEST/"

# Seed config/.env only if missing on the host. Missing $SRC/.env fails loudly
# here rather than silently producing a broken install.
[[ -f "$DEST/.env" ]]       || cp "$SRC/.env"             "$DEST/.env"
[[ -f "$DEST/config.yml" ]] || cp "$SRC/config.example.yml" "$DEST/config.yml"

if [[ ! -x "$DEST/.venv/bin/pip" ]]; then
    # Debian/Ubuntu ships `python3 -m venv` in a separate, version-specific
    # package. Install it idempotently; no-op on non-apt distros.
    if command -v apt-get >/dev/null 2>&1; then
        PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        waited=0
        while pgrep -x apt >/dev/null || pgrep -x apt-get >/dev/null \
           || pgrep -x dpkg >/dev/null; do
            (( waited >= 300 )) && { echo "apt still busy after 5m; skipping auto-install" >&2; break; }
            echo "apt/dpkg busy, waiting... (${waited}s)"
            sleep 10
            waited=$((waited + 10))
        done
        DEBIAN_FRONTEND=noninteractive apt-get update -qq || true
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            "python${PY_VER}-venv" python3-venv || true
    fi
    rm -rf "$DEST/.venv"
    python3 -m venv "$DEST/.venv"
fi
"$DEST/.venv/bin/pip" install --quiet --upgrade pip
"$DEST/.venv/bin/pip" install --quiet -r "$DEST/requirements.txt"

mkdir -p "$DEST/logs" "$DEST/state"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DEST"
chmod 600 "$DEST/.env"

cp "$DEST/deploy/vps-sentry.service" "$UNIT"
systemctl daemon-reload
systemctl enable vps-sentry.service
systemctl restart vps-sentry.service

cat <<EOF
vps-sentry installed at $DEST
  config:     $DEST/config.yml
  secrets:    $DEST/.env
  tick log:   $DEST/logs/vps-sentry.log
  event log:  journalctl -u vps-sentry -f
  service:    systemctl {status,restart} vps-sentry
EOF
