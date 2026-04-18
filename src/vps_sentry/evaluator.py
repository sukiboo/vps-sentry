from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import METRIC_DIRECTION, Alert, Config, Snapshot, Tier
from .state import AlertState

# Metrics other than disk_used use a (metric, None) key; disk is per-mount.
NON_DISK_METRICS = [
    "load_per_core",
    "memory_used",
    "swap_used",
    "iowait",
]


def evaluate(
    snapshot: Snapshot,
    state: AlertState,
    cfg: Config,
    now: datetime | None = None,
) -> list[Alert]:
    now = now or datetime.now(timezone.utc)
    alerts: list[Alert] = []

    metric_keys: list[tuple[str, str | None]] = [
        (m, None) for m in NON_DISK_METRICS if m in cfg.thresholds
    ]
    if "disk_used" in cfg.thresholds:
        metric_keys.extend(("disk_used", mount) for mount in snapshot.disk_used)

    in_warmup = len(state.history) < cfg.sustained_checks

    for metric, mount in metric_keys:
        tiers = cfg.thresholds[metric]
        warn_t = float(tiers["warn"])
        crit_t = float(tiers["critical"])
        direction = METRIC_DIRECTION[metric]
        value = _value(snapshot, metric, mount)
        current_tier = _classify(value, direction, warn_t, crit_t)

        key = (metric, mount)
        active_tier = state.active.get(key)

        if current_tier is None:
            if active_tier is not None:
                prev_threshold = crit_t if active_tier == "critical" else warn_t
                alerts.append(
                    Alert(
                        metric=metric,
                        tier=active_tier,
                        kind="recover",
                        value=value if value is not None else 0.0,
                        threshold=prev_threshold,
                        mount=mount,
                        snapshot=snapshot,
                    )
                )
                state.active.pop(key, None)
            continue

        assert value is not None  # _classify returns None iff value is None

        if in_warmup:
            continue

        if not _sustained(
            state, metric, mount, current_tier, direction, warn_t, crit_t, cfg.sustained_checks
        ):
            continue

        cd_key = (metric, mount, current_tier)
        last_fired = state.cooldowns.get(cd_key)
        if last_fired and now - last_fired < timedelta(minutes=cfg.cooldown_minutes):
            continue

        threshold = crit_t if current_tier == "critical" else warn_t
        alerts.append(
            Alert(
                metric=metric,
                tier=current_tier,
                kind="fire",
                value=value,
                threshold=threshold,
                mount=mount,
                snapshot=snapshot,
            )
        )
        state.cooldowns[cd_key] = now
        state.active[key] = current_tier

    return alerts


def _value(snapshot: Snapshot, metric: str, mount: str | None) -> float | None:
    if metric == "load_per_core":
        return snapshot.load_per_core
    if metric == "memory_used":
        return snapshot.mem_used
    if metric == "swap_used":
        return snapshot.swap_used
    if metric == "iowait":
        return snapshot.iowait
    if metric == "disk_used":
        return snapshot.disk_used.get(mount) if mount else None
    raise ValueError(f"unknown metric {metric!r}")


def _classify(value: float | None, direction: str, warn_t: float, crit_t: float) -> Optional[Tier]:
    if value is None:
        return None
    if direction == "high":
        if value >= crit_t:
            return "critical"
        if value >= warn_t:
            return "warn"
    else:  # "low"
        if value <= crit_t:
            return "critical"
        if value <= warn_t:
            return "warn"
    return None


def _sustained(
    state: AlertState,
    metric: str,
    mount: str | None,
    target_tier: Tier,
    direction: str,
    warn_t: float,
    crit_t: float,
    sustained_checks: int,
) -> bool:
    """Every one of the last N snapshots must classify at target_tier or worse."""
    history = list(state.history)[-sustained_checks:]
    if len(history) < sustained_checks:
        return False
    for snap in history:
        tier = _classify(_value(snap, metric, mount), direction, warn_t, crit_t)
        if tier is None:
            return False
        if target_tier == "critical" and tier != "critical":
            return False
    return True
