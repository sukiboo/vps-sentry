#!/usr/bin/env bash
# Deploy vps-sentry to every host listed under `hosts:` in config.yml.
# Each key under `hosts:` is used as both the SSH target and the daemon's
# match key (socket.gethostname()) — set up ~/.ssh/config aliases accordingly.
# Requires: SSH access + sudo on each host (passwordless or interactive —
# you'll be prompted per host otherwise), rsync on both ends.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="$REPO_ROOT/.env"
CONFIG_FILE="$REPO_ROOT/config.yml"
STAGE_REMOTE="/tmp/vps-sentry-stage"

[[ -f "$ENV_FILE" ]] || { echo "missing $ENV_FILE" >&2; exit 1; }
[[ -f "$CONFIG_FILE" ]] || { echo "missing $CONFIG_FILE" >&2; exit 1; }

# Read the host list (keys under `hosts:`) from config.yml.
PY="$REPO_ROOT/.venv/bin/python3"
[[ -x "$PY" ]] || PY=python3
mapfile -t HOSTS < <("$PY" - "$CONFIG_FILE" <<'PYEOF'
import sys, yaml
with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f) or {}
for h in (cfg.get("hosts") or {}):
    print(h)
PYEOF
)
(( ${#HOSTS[@]} > 0 )) || { echo "no \`hosts:\` entries in $CONFIG_FILE" >&2; exit 1; }

# Build a local staging dir once; same payload goes to every host.
STAGE_LOCAL=$(mktemp -d)
trap 'rm -rf "$STAGE_LOCAL"' EXIT

rsync -a \
    --exclude='.venv' --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.mypy_cache' --exclude='.ruff_cache' --exclude='.pytest_cache' \
    --exclude='.vscode' --exclude='.idea' --exclude='.claude' \
    --exclude='.pre-commit-config.yaml' --exclude='*.egg-info' \
    --exclude='logs' --exclude='state' --exclude='.env' \
    "$REPO_ROOT/" "$STAGE_LOCAL/"

cp "$ENV_FILE" "$STAGE_LOCAL/.env"

declare -a ok=() failed=()
for host in "${HOSTS[@]}"; do
    printf '==> deploying vps-sentry on `%s`...\n' "$host"
    if rsync -a --delete -e ssh "$STAGE_LOCAL/" "$host:$STAGE_REMOTE/" \
        && ssh -t "$host" "sudo bash $STAGE_REMOTE/deploy/install.sh $STAGE_REMOTE" \
        && ssh "$host" "rm -rf $STAGE_REMOTE"; then
        ok+=("$host")
    else
        failed+=("$host")
        echo "!! $host failed" >&2
    fi
done

echo
echo "deployed: ${#ok[@]}  failed: ${#failed[@]}"
(( ${#failed[@]} == 0 )) || { printf '  failed: %s\n' "${failed[@]}"; exit 1; }
