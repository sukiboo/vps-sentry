from __future__ import annotations

import html
import logging
import time

import requests

from .models import Alert, Config, ProcInfo

log = logging.getLogger(__name__)

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

METRIC_LABELS: dict[str, str] = {
    "load_per_core": "load per core",
    "memory_used": "memory used",
    "swap_used": "swap used",
    "disk_used": "disk used",
    "iowait": "iowait",
}

TIER_PREFIX = {"warn": "⚠️", "critical": "🚨", "recover": "✅"}


def send(
    cfg: Config,
    alert: Alert,
    top_cpu: list[ProcInfo],
    top_mem: list[ProcInfo],
    dry_run: bool = False,
) -> None:
    text = format_alert(cfg, alert, top_cpu, top_mem)
    if dry_run:
        print(f"[dry-run] would send:\n{text}\n")
        return
    _post(cfg, text)


def send_text(cfg: Config, text: str, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[dry-run] would send:\n{text}\n")
        return
    _post(cfg, text)


def _post(cfg: Config, text: str) -> None:
    url = TELEGRAM_URL.format(token=cfg.telegram_token)
    payload = {
        "chat_id": cfg.telegram_chat_id,
        "text": f"<code>{html.escape(text)}</code>",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    backoff = 1.0
    for attempt in range(1, 4):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if 200 <= r.status_code < 300:
                return
            log.warning("Telegram %s (attempt %d): %s", r.status_code, attempt, r.text[:200])
        except requests.RequestException as exc:
            log.warning("Telegram request failed (attempt %d): %s", attempt, exc)
        if attempt < 3:
            time.sleep(backoff)
            backoff *= 2
    log.error("Telegram send gave up after 3 attempts")


def format_alert(
    cfg: Config,
    alert: Alert,
    top_cpu: list[ProcInfo],
    top_mem: list[ProcInfo],
) -> str:
    label = METRIC_LABELS.get(alert.metric, alert.metric)
    if alert.mount:
        label = f"{label} ({alert.mount})"

    ts = alert.snapshot.ts.strftime("%Y-%m-%d %H:%M UTC") if alert.snapshot else ""
    prefix = TIER_PREFIX[alert.tier]
    value = _fmt_value(alert.metric, alert.value)
    header = f"{prefix}  {ts} -- {label} {value} on `{cfg.host}`"

    if alert.tier == "recover":
        return header

    body = [header, ""]
    if top_mem:
        body.append("Top by RAM:")
        body.extend(f"  {_fmt_rss(p.rss_bytes)}  {_short_cmd(p)}" for p in top_mem)
        body.append("")
    if top_cpu:
        cpu_count = max(1, alert.snapshot.cpu_count) if alert.snapshot else 1
        body.append("Top by CPU:")
        body.extend(f"  {p.cpu_pct / cpu_count:4.0f}%  {_short_cmd(p)}" for p in top_cpu)
    return "\n".join(body).rstrip()


def _fmt_value(metric: str, value: float) -> str:
    if metric == "load_per_core":
        return f"{value:.2f}"
    return f"{value:.0f}%"


def _fmt_rss(rss: int) -> str:
    gb = rss / (1024**3)
    if gb >= 1:
        return f"{gb:4.1f} GB"
    mb = rss / (1024**2)
    return f"{mb:4.0f} MB"


def _short_cmd(p: ProcInfo, limit: int = 40) -> str:
    cmd = p.cmdline or p.name
    return cmd if len(cmd) <= limit else cmd[: limit - 1] + "…"
