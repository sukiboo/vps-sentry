from __future__ import annotations

import html
import logging
import time
from typing import NamedTuple

import requests

from .models import Alert, Config, ProcInfo

log = logging.getLogger(__name__)

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

RETRY_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 10
INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0

CMDLINE_LIMIT = 40


class TierStyle(NamedTuple):
    prefix: str
    silent: bool


TIERS: dict[str, TierStyle] = {
    "warn": TierStyle(prefix="⚠️", silent=True),
    "critical": TierStyle(prefix="🚨", silent=False),
    "recover": TierStyle(prefix="✅", silent=False),
}


def send(
    cfg: Config,
    alert: Alert,
    top_cpu: list[ProcInfo],
    top_mem: list[ProcInfo],
    dry_run: bool = False,
) -> None:
    text = format_alert(cfg, alert, top_cpu, top_mem)
    silent = TIERS[alert.tier].silent
    if dry_run:
        print(f"[dry-run] would send{' (silent)' if silent else ''}:\n{text}\n")
        return
    _post(cfg, text, silent=silent)


def send_text(cfg: Config, text: str, dry_run: bool = False, silent: bool = False) -> None:
    if dry_run:
        print(f"[dry-run] would send{' (silent)' if silent else ''}:\n{text}\n")
        return
    _post(cfg, text, silent=silent)


def _post(cfg: Config, text: str, silent: bool = False) -> None:
    url = TELEGRAM_URL.format(token=cfg.telegram_token)
    payload = {
        "chat_id": cfg.telegram_chat_id,
        "text": f"<code>{html.escape(text)}</code>",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": silent,
    }
    backoff = INITIAL_BACKOFF_SECONDS
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            if 200 <= r.status_code < 300:
                return
            log.warning("Telegram %s (attempt %d): %s", r.status_code, attempt, r.text[:200])
        except requests.RequestException as exc:
            log.warning("Telegram request failed (attempt %d): %s", attempt, exc)
        if attempt < RETRY_ATTEMPTS:
            time.sleep(backoff)
            backoff *= BACKOFF_MULTIPLIER
    log.error("Telegram send gave up after %d attempts", RETRY_ATTEMPTS)


def format_alert(
    cfg: Config,
    alert: Alert,
    top_cpu: list[ProcInfo],
    top_mem: list[ProcInfo],
) -> str:
    label = alert.metric.replace("_", " ")
    if alert.mount:
        label = f"{label} ({alert.mount})"

    ts = alert.snapshot.ts.strftime("%Y-%m-%d %H:%M UTC") if alert.snapshot else ""
    prefix = TIERS[alert.tier].prefix
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


def _short_cmd(p: ProcInfo, limit: int = CMDLINE_LIMIT) -> str:
    cmd = p.cmdline or p.name
    return cmd if len(cmd) <= limit else cmd[: limit - 1] + "…"
