from __future__ import annotations

import os
import socket
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .models import METRIC_DIRECTION, Config

REQUIRED_METRICS = set(METRIC_DIRECTION.keys())


def load_config(config_path: str | Path, env_path: str | Path | None = None) -> Config:
    config_path = Path(config_path)
    if env_path is not None:
        load_dotenv(env_path)
    else:
        # Prefer .env next to the config file, fall back to CWD.
        candidate = config_path.parent / ".env"
        load_dotenv(candidate if candidate.exists() else None)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_ALERTS_CHANNEL", "").strip()
    if not token or not chat_id:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_ALERTS_CHANNEL must be set in the environment or .env"
        )

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    thresholds = raw.get("thresholds") or {}
    _validate_thresholds(thresholds)

    mounts = raw.get("mounts") or ["/"]
    if not isinstance(mounts, list) or not all(isinstance(m, str) for m in mounts):
        raise ValueError("`mounts` must be a list of strings")

    return Config(
        interval_seconds=int(raw.get("interval_seconds", 60)),
        sustained_checks=int(raw.get("sustained_checks", 3)),
        cooldown_minutes=int(raw.get("cooldown_minutes", 30)),
        show_top_n_proc=int(raw.get("show_top_n_proc", 5)),
        thresholds=thresholds,
        mounts=mounts,
        telegram_token=token,
        telegram_chat_id=chat_id,
        host=str(raw.get("host") or socket.gethostname()),
        config_path=str(config_path),
    )


def _validate_thresholds(thresholds: dict) -> None:
    missing = REQUIRED_METRICS - set(thresholds.keys())
    if missing:
        raise ValueError(f"config.yml missing thresholds for: {sorted(missing)}")

    for metric, tiers in thresholds.items():
        if metric not in METRIC_DIRECTION:
            raise ValueError(f"Unknown metric in thresholds: {metric!r}")
        if not {"warn", "critical"}.issubset(tiers):
            raise ValueError(f"{metric} must define both `warn` and `critical`")
        warn = float(tiers["warn"])
        crit = float(tiers["critical"])
        direction = METRIC_DIRECTION[metric]
        # "high": breach when value > threshold, so critical > warn
        # "low":  breach when value < threshold, so critical < warn
        if direction == "high" and not crit > warn:
            raise ValueError(f"{metric}: critical ({crit}) must be greater than warn ({warn})")
        if direction == "low" and not crit < warn:
            raise ValueError(f"{metric}: critical ({crit}) must be less than warn ({warn})")
