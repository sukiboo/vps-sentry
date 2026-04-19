# vps-sentry

Lightweight Python daemon that monitors VPS resources and sends Telegram alerts. Built to stay out of the way: <30 MB RSS, <1% CPU average, supervised by systemd.

## What it watches

Every `interval_seconds` it samples via `psutil` and evaluates thresholds for:

- **load_per_core** — 1-min load average / CPU count
- **memory_used**, **swap_used**, **disk_used** (per mount), **iowait** — percentages

Alerts are suppressed until `sustained_checks` consecutive samples are over threshold, and each (metric, tier) has a `cooldown_minutes` to prevent spam. Warn can escalate to critical independently. One-shot ✅ recovery message when a metric drops back under.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp config.example.yml config.yml           # tune thresholds to taste
echo 'TELEGRAM_BOT_TOKEN=…' > .env          # from @BotFather
echo 'TELEGRAM_ALERTS_CHANNEL=…' >> .env    # numeric chat id or @channel

# one-shot snapshot (sanity check)
PYTHONPATH=src .venv/bin/python -m vps_sentry --once

# run the loop, print alerts instead of sending Telegram
PYTHONPATH=src .venv/bin/python -m vps_sentry --dry-run

# run for real
PYTHONPATH=src .venv/bin/python -m vps_sentry
```

## Logs

- **Event log** (startup, shutdown, errors, Telegram retries) → stdout / journald.
- **Tick log** (one JSON line per sample) → `logs/vps-sentry.log`, rotated daily, 60-day retention. Percentage metrics are written as 0–1 ratios at 4 decimal places.

```bash
tail -f logs/vps-sentry.log | jq .
```

## Deploy

```bash
sudo ./deploy/install.sh
```

Idempotent installer: creates a `monitor` system user, rsyncs to `/opt/vps-monitor`, builds a venv, installs the systemd unit, enables it. Live-tail with `journalctl -u vps-monitor -f`.

See [`.claude/CLAUDE.md`](.claude/CLAUDE.md) for implementation notes.
