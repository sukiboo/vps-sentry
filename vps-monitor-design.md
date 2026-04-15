# VPS Resource Monitor — Design Doc

A lightweight Python daemon that samples system resources on a fixed interval and sends Telegram alerts when thresholds are breached. Designed to add negligible load to the host.

## Goals & non-goals

**Goals**
- Detect and alert on CPU, memory, disk, and swap pressure before things break.
- Run continuously with very low resource footprint (target: <1% CPU avg, <30 MB RAM).
- Avoid alert spam — one message per incident, not one per check.
- Give enough context in each alert to diagnose without SSHing in immediately.

**Non-goals**
- Historical metrics dashboards (use Netdata or Prometheus if you want graphs).
- Per-bot application-level health (that's the bot's job).
- Multi-host monitoring.

## Architecture

A single long-running Python process, supervised by systemd. One main loop:

```
  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
  │ collect  │ -> │ evaluate │ -> │  notify  │ -> │  sleep   │
  └──────────┘    └──────────┘    └──────────┘    └──────────┘
       ^                                                |
       └────────────────────────────────────────────────┘
```

**Why long-running rather than cron:** lets you track state across ticks (alert cooldowns, "sustained for N checks" logic) without a state file getting hammered, and avoids repeated Python startup cost. The tradeoff is you need systemd to keep it alive, which is trivial.

### Modules

- `collector.py` — wraps `psutil` calls, returns a single `Snapshot` dataclass per tick.
- `evaluator.py` — pure function: `(snapshot, history, config) -> list[Alert]`. Stateless logic; all state lives in the in-memory history buffer.
- `notifier.py` — sends Telegram messages, handles transient HTTP failures.
- `state.py` — in-memory ring buffer of recent snapshots + per-metric alert cooldown timestamps. Optionally flushed to a small JSON file on shutdown so cooldowns survive restarts.
- `config.py` — loads from a YAML or `.env` file.
- `main.py` — wires it all together, runs the loop.

## Metrics to collect

All via `psutil` — no shelling out, much cheaper than parsing `top`/`vmstat`.

| Metric | Source | Why it matters |
|---|---|---|
| CPU load avg (1, 5, 15 min) | `psutil.getloadavg()` | Best signal for sustained CPU pressure |
| CPU % | `psutil.cpu_percent(interval=None)` | Spot spikes |
| Memory available % | `psutil.virtual_memory().available` | "Free" is misleading; available is what counts |
| Swap used % | `psutil.swap_memory()` | Any active swap usage on a bot VPS = trouble |
| Disk used % per mount | `psutil.disk_usage('/')` (and others) | Full disk = silent bot failure |
| Disk I/O wait % | `psutil.cpu_times_percent().iowait` | High iowait means disk is the bottleneck |
| Process count | `len(psutil.pids())` | Detect runaway forking |

When an alert fires, also grab the **top 5 processes by CPU and by memory** to include in the message — this is the diagnostic context that makes alerts actionable.

## Alert logic

Three rules to prevent noise:

1. **Sustained breach.** A metric must exceed its threshold for N consecutive checks before alerting (e.g., 3 checks = 3 minutes at a 60s interval). Filters out brief spikes.
2. **Cooldown.** After alerting on a metric, suppress further alerts on it for M minutes (e.g., 30 min). Prevents spam if the condition persists.
3. **Recovery alert.** When a metric returns below threshold after an active alert, send a short "recovered" message. Tells you the incident is over without needing to check.

Two threshold tiers per metric — `warn` and `critical` — sent with different prefixes (⚠️ vs 🚨). Critical breaches bypass cooldown of warn-level alerts for the same metric.

## Telegram integration

Just HTTP POST to `https://api.telegram.org/bot<TOKEN>/sendMessage`. No SDK needed — `requests` or `httpx` is enough; or stdlib `urllib` if you want zero deps.

Setup steps for the user:
1. Create a bot via `@BotFather`, get the token.
2. Send any message to the bot, then GET `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat_id`.
3. Put both in the config.

Message format (Markdown):
```
🚨 *CRITICAL* — memory available 4%
Host: my-vps  |  2026-04-15 14:23 UTC

Top by RAM:
  1.2 GB  python bot_arbitrage.py
  0.8 GB  python bot_scraper.py
  0.4 GB  postgres

Top by CPU:
  87%  python bot_arbitrage.py
  ...
```

Failure handling: if Telegram returns non-2xx or the request times out, log it and retry up to 3 times with backoff. Never let a notify failure crash the loop.

## Configuration

YAML file at `/etc/vps-monitor/config.yml` (or alongside the script). Example:

```yaml
interval_seconds: 60
sustained_checks: 3
cooldown_minutes: 30

telegram:
  token: "..."
  chat_id: "..."

thresholds:
  load_per_core:    { warn: 1.0, critical: 2.0 }
  memory_avail_pct: { warn: 15,  critical: 5 }     # alert when BELOW
  swap_used_pct:    { warn: 10,  critical: 50 }
  disk_used_pct:    { warn: 80,  critical: 90 }
  iowait_pct:       { warn: 20,  critical: 40 }

mounts: ["/", "/var"]
```

## Resource budget for the script itself

To stay lightweight:

- **Polling interval: 60s by default.** More frequent checks rarely buy you anything for bot workloads and add overhead.
- **Use `psutil` non-blocking calls.** Specifically `cpu_percent(interval=None)` — passing an interval makes it sleep, which is fine but unnecessary inside an already-paced loop.
- **No external DB.** A ~100-entry ring buffer in memory is enough history for the sustained-check logic. Roughly a few KB.
- **Single process, single thread.** Async or threading is overkill; one tick takes milliseconds.
- **Minimal deps.** `psutil` + `pyyaml` + (optional) `requests`. Total install is small.
- **Log sparingly.** INFO on startup/shutdown and on alerts only. Don't log every tick — fills disk over weeks.

Expected footprint: idle most of the time, brief work every 60s. Should sit under 30 MB RSS and well under 1% CPU averaged.

## Deployment

Run as a systemd service so it restarts on crash and on reboot:

```ini
# /etc/systemd/system/vps-monitor.service
[Unit]
Description=VPS Resource Monitor
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/vps-monitor/main.py
Restart=on-failure
RestartSec=10s
User=monitor
Nice=10                 # de-prioritize so it never competes with bots
MemoryMax=64M           # belt-and-suspenders cap

[Install]
WantedBy=multi-user.target
```

`Nice=10` and `MemoryMax=64M` are the key bits for "don't interfere with the actual workload."

Logs go to journald automatically — view with `journalctl -u vps-monitor -f`.

## Edge cases worth handling

- **Clock jumps / suspend-resume.** If the loop wakes up and discovers a huge gap since last tick, skip the sustained-check logic for that tick (history is stale).
- **First N ticks after startup.** Don't alert until the history buffer has at least `sustained_checks` entries — avoids a false alarm on launch.
- **Disk mount disappears.** Wrap each `disk_usage()` call in a try/except; log and continue.
- **Telegram unreachable for extended period.** Queue alerts (cap the queue at, say, 10) and flush when connectivity returns. Drop oldest if queue overflows.
- **Self-test on startup.** Send a "monitor started on `<host>`" Telegram message so you know setup works and survive restarts are visible.

## Optional extras (defer until needed)

- A `/healthz` heartbeat that pings a service like healthchecks.io so you're alerted if the *monitor itself* dies.
- Per-process alerts (e.g., "bot X has been using >2 GB for 10 min").
- Network throughput tracking via `psutil.net_io_counters()`.
- A `--dry-run` flag that prints alerts instead of sending them, for tuning thresholds.

## Build order suggestion

1. `collector.py` + a script that prints a snapshot — verify metrics look right.
2. `notifier.py` + manually trigger a test message — verify Telegram works.
3. `evaluator.py` with simple threshold logic, no cooldowns yet.
4. Wire up `main.py`, run in foreground, induce load with `stress` to verify alerts fire.
5. Add cooldowns + sustained-check + recovery messages.
6. Write systemd unit, deploy, leave it for a day, tune thresholds based on what fires.
