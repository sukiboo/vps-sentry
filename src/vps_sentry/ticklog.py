from __future__ import annotations

import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from .models import Snapshot


def setup(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("vps_sentry.tick")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # Idempotent: avoid stacking handlers if called twice (tests, reloads).
    if not any(isinstance(h, TimedRotatingFileHandler) for h in logger.handlers):
        handler = TimedRotatingFileHandler(
            log_dir / "vps-sentry.log",
            when="midnight",
            backupCount=60,
            utc=True,
            encoding="utf-8",
        )
        handler.suffix = "%Y-%m-%d"
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


def log_tick(logger: logging.Logger, snap: Snapshot, n_alerts: int) -> None:
    # Hand-formatted so trailing zeros (e.g. 0.3100) survive — json.dumps drops them.
    # Percentage metrics are stored 0–100 internally but logged as 0–1 ratios.
    disk = ", ".join(f"{json.dumps(m)}: {v / 100:.4f}" for m, v in snap.disk_used.items())
    line = (
        f'{{"ts": "{snap.ts.strftime("%Y-%m-%dT%H:%M:%SZ")}", '
        f'"cpu_used": {snap.cpu_used / 100:.4f}, '
        f'"load_per_core": {snap.load_per_core:.4f}, '
        f'"mem_used": {snap.mem_used / 100:.4f}, '
        f'"swap_used": {snap.swap_used / 100:.4f}, '
        f'"disk_used": {{{disk}}}, '
        f'"iowait": {snap.iowait / 100:.4f}, '
        f'"proc": {snap.proc_count}, '
        f'"alerts": {n_alerts}}}'
    )
    logger.info(line)
