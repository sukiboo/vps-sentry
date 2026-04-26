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

    raw = _apply_host_override(raw)

    required = [
        "interval_seconds",
        "sustained_checks",
        "cooldown_minutes",
        "show_top_n_proc",
        "thresholds",
        "mounts",
        "weekly_report",
    ]
    missing = [k for k in required if k not in raw]
    if missing:
        raise ValueError(f"config.yml missing required fields: {missing}")

    _validate_thresholds(raw["thresholds"])

    mounts = raw["mounts"]
    if not isinstance(mounts, list) or not all(isinstance(m, str) for m in mounts):
        raise ValueError("`mounts` must be a list of strings")

    weekly = _parse_weekly_report(raw["weekly_report"])

    return Config(
        interval_seconds=int(raw["interval_seconds"]),
        sustained_checks=int(raw["sustained_checks"]),
        cooldown_minutes=int(raw["cooldown_minutes"]),
        show_top_n_proc=int(raw["show_top_n_proc"]),
        thresholds=raw["thresholds"],
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


def _apply_host_override(raw: dict) -> dict:
    hosts_block = raw.pop("hosts", None)
    if hosts_block in (None, {}):
        return raw
    if not isinstance(hosts_block, dict):
        raise ValueError("`hosts` must be a mapping of hostname -> overrides")
    hostname = socket.gethostname()
    if hostname not in hosts_block:
        raise ValueError(
            f"hostname {hostname!r} (from socket.gethostname()) is not listed in the "
            f"`hosts` section of config.yml; defined: {sorted(hosts_block.keys())}"
        )
    override = hosts_block[hostname] or {}
    if not isinstance(override, dict):
        raise ValueError(f"`hosts.{hostname}` must be a mapping")
    return _deep_merge(raw, override)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _parse_weekly_report(wr: dict) -> dict:
    if not isinstance(wr, dict):
        raise ValueError("`weekly_report` must be a mapping")
    missing = [k for k in ("enabled", "day", "hour", "minute") if k not in wr]
    if missing:
        raise ValueError(f"weekly_report missing required fields: {missing}")
    enabled = wr["enabled"]
    if not isinstance(enabled, bool):
        raise ValueError(f"weekly_report.enabled must be a bool, got {enabled!r}")
    day = wr["day"]
    if isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6:
        raise ValueError(f"weekly_report.day must be 0-6 (Mon=0..Sun=6), got {day!r}")
    hour = int(wr["hour"])
    minute = int(wr["minute"])
    if not 0 <= hour < 24:
        raise ValueError(f"weekly_report.hour must be 0-23, got {hour}")
    if not 0 <= minute < 60:
        raise ValueError(f"weekly_report.minute must be 0-59, got {minute}")
    return {"enabled": enabled, "day": day, "hour": hour, "minute": minute}


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
