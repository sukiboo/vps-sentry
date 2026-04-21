from __future__ import annotations

import os
import socket
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .models import METRIC_DIRECTION, Config

REQUIRED_METRICS = set(METRIC_DIRECTION.keys())

_WEEKDAY_NAMES: dict[str, int] = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


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

    weekly = _parse_weekly_report(raw.get("weekly_report") or {})

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
        weekly_report_enabled=weekly["enabled"],
        weekly_report_day=weekly["day"],
        weekly_report_hour=weekly["hour"],
        weekly_report_minute=weekly["minute"],
        config_path=str(config_path),
    )


def _parse_weekly_report(wr: dict) -> dict:
    if not isinstance(wr, dict):
        raise ValueError("`weekly_report` must be a mapping")
    enabled = bool(wr.get("enabled", True))
    day = _parse_weekday(wr.get("day", "sunday"))
    hour = int(wr.get("hour", 0))
    minute = int(wr.get("minute", 0))
    if not 0 <= hour < 24:
        raise ValueError(f"weekly_report.hour must be 0-23, got {hour}")
    if not 0 <= minute < 60:
        raise ValueError(f"weekly_report.minute must be 0-59, got {minute}")
    return {"enabled": enabled, "day": day, "hour": hour, "minute": minute}


def _parse_weekday(value) -> int:
    if isinstance(value, bool):
        raise ValueError(f"weekly_report.day must be 0-6 or a weekday name, got {value!r}")
    if isinstance(value, int):
        if 0 <= value <= 6:
            return value
        raise ValueError(f"weekly_report.day must be 0-6, got {value}")
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _WEEKDAY_NAMES:
            return _WEEKDAY_NAMES[key]
    raise ValueError(f"weekly_report.day must be 0-6 or a weekday name, got {value!r}")


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
