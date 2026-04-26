# vps-sentry

Lightweight Python daemon that monitors VPS resources and sends Telegram alerts. Built to stay out of the way: <30 MB RSS, <1% CPU average, supervised by systemd.

## What it watches

Every `interval_seconds` it samples via `psutil` and evaluates thresholds for:

- **load_per_core** — 1-min load average / CPU count
- **memory_used**, **swap_used**, **disk_used** (per mount), **iowait** — percentages

Alerts are suppressed until `sustained_checks` consecutive samples are over threshold, and each (metric, tier) has a `cooldown_minutes` to prevent spam. Warn can escalate to critical independently. One-shot ✅ recovery message when a metric drops back under.

## Telegram setup

You need a bot token and a chat ID before the daemon can send anything. One-time setup, ~2 minutes:

**1. Create the bot.** In Telegram, open a chat with [@BotFather](https://t.me/BotFather) and send `/newbot`. Pick a display name and a username ending in `bot`. BotFather replies with an HTTP API token that looks like `123456789:ABCdefGhIJKlmNOpqrstuv-WxyZ`. That's your `TELEGRAM_BOT_TOKEN`.

**2. Decide where alerts go.** Three options:

- **DM to yourself** — easiest. Just message your new bot (say anything) to open the chat, then fetch your user ID from `https://api.telegram.org/bot<TOKEN>/getUpdates` — look for `"chat":{"id":<number>, ...}`. That number is your `TELEGRAM_ALERTS_CHANNEL`.
- **Private group** — create a group, add your bot to it, send any message, then hit `getUpdates` as above. The chat ID will be negative (e.g. `-987654321`).
- **Public channel** — create a channel, add your bot as an admin with "Post Messages" permission, and use the handle (e.g. `@my_alerts_channel`) as `TELEGRAM_ALERTS_CHANNEL`.

**3. Write `.env`.**

```bash
cp .env.example .env
# edit .env and paste in TELEGRAM_BOT_TOKEN and TELEGRAM_ALERTS_CHANNEL
```

**4. Sanity check.**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.yml config.yml

# either delete the example `hosts:` block from config.yml, or add this machine:
#   hosts:
#     <output of `hostname`>: {}

# should print one snapshot and exit, no Telegram traffic
PYTHONPATH=src .venv/bin/python -m vps_sentry --once
```

The daemon validates `socket.gethostname()` against the `hosts:` block on startup and refuses to run if it isn't listed (strict-when-present, see "Multiple VPSes" below). Removing the block is fine for local dev — it just means "same config everywhere."

If that works, force a test alert by creating a breaching config (e.g. `memory_used: { warn: 1, critical: 99 }`, `sustained_checks: 2`, `interval_seconds: 2`) and running `--dry-run` — you'll see the alert text on stdout. Drop `--dry-run` to send it to Telegram for real.

## Quick start

```bash
# after completing Telegram setup above:

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

**Single VPS** — copy the repo to the host and run the installer as root:

```bash
sudo ./deploy/install.sh
```

**Multiple VPSes** — list hosts under `hosts:` in `config.yml` (keyed by each box's `socket.gethostname()`, with optional per-host overrides in the value):

```yaml
# in config.yml
hosts:
  vps-prod-1: {}
  vps-bot-2:
    thresholds:
      memory_used: { warn: 90, critical: 95 }
```

Then run the wrapper locally:

```bash
./deploy/deploy-all.sh
```

The wrapper reads the `hosts:` keys from `config.yml`, rsyncs the repo to each host, runs `install.sh` over SSH, and prints a per-host success/failure summary. Requires SSH access + sudo on every target (passwordless sudo or root logins are smoothest; otherwise you'll be prompted for the sudo password per host). Each `hosts:` key doubles as the SSH target, so configure matching `~/.ssh/config` entries (multiple names per `Host` line are fine — e.g. `Host sail lightsail`). The daemon validates on startup that its own `socket.gethostname()` is listed in `hosts:`, so a missed entry fails loudly rather than silently using defaults.

Either path produces the same idempotent install: a `vps-sentry` system user, rsync to `/opt/vps-sentry`, venv built, systemd unit enabled.

Prereqs on the target VPS: `python3` and `rsync`. On Debian/Ubuntu, `python3 -m venv` also needs the matching `pythonX.Y-venv` package — `install.sh` tries to `apt install` it automatically (idempotent; no-op on non-apt distros). If your package lists are stale and the auto-install fails, run `sudo apt update` on the host and re-deploy.

Everything on the VPS lives under **`/opt/vps-sentry`**:

| Path | What |
|---|---|
| `/opt/vps-sentry/config.yml` | thresholds, intervals, mounts |
| `/opt/vps-sentry/.env` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALERTS_CHANNEL` |
| `/opt/vps-sentry/logs/vps-sentry.log` | per-tick JSON Lines, daily rotation, 60d retention |
| `journalctl -u vps-sentry -f` | event log (startup, alerts, errors) |
| `systemctl {status,restart} vps-sentry` | service control |

The install path is intentionally hardcoded to `/opt/vps-sentry` (FHS-standard location for add-on packages). If you need a different path, change `DEST=` in `deploy/install.sh` and the four matching paths in `deploy/vps-sentry.service`.

See [`.claude/CLAUDE.md`](.claude/CLAUDE.md) for implementation notes.
