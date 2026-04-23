#!/usr/bin/env bash
# Deploy vps-sentry to every host listed in VPS_HOSTS (from .env).
# Requires: SSH access + sudo on each host (passwordless or interactive —
# you'll be prompted per host otherwise), rsync on both ends.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="$REPO_ROOT/.env"
STAGE_REMOTE="/tmp/vps-sentry-stage"

[[ -f "$ENV_FILE" ]] || { echo "missing $ENV_FILE" >&2; exit 1; }
set -a; source "$ENV_FILE"; set +a
: "${VPS_HOSTS:?set VPS_HOSTS in .env (space-separated SSH targets)}"

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

# Ship a cleaned .env — drop VPS_HOSTS so the host list doesn't leak to each VPS.
grep -v '^VPS_HOSTS=' "$ENV_FILE" > "$STAGE_LOCAL/.env"

declare -a ok=() failed=()
for host in $VPS_HOSTS; do
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
