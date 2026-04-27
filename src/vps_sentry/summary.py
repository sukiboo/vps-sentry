from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

log = logging.getLogger(__name__)

TICK_LOG_PREFIX = "vps-sentry.log"

SCALAR_METRICS: tuple[str, ...] = (
    "cpu_used",
    "load_per_core",
    "mem_used",
    "swap_used",
    "iowait",
)

SCALAR_ROWS: tuple[tuple[str, str], ...] = (
    ("load", "load_per_core"),
    ("cpu", "cpu_used"),
    ("memory", "mem_used"),
    ("swap", "swap_used"),
    ("iowait", "iowait"),
)


def schedule_next_after(dt: datetime, day: int, hour: int, minute: int) -> datetime:
    """Return the next datetime strictly after `dt` that falls on the given weekday
    (Mon=0..Sun=6) at the given hour:minute. Timezone is preserved from `dt`."""
    candidate = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (day - candidate.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= dt:
        candidate += timedelta(days=7)
    return candidate


def read_last_sent(path: Path) -> datetime | None:
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        log.warning("bad timestamp in %s: %r", path, raw)
        return None


def write_last_sent(path: Path, dt: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", dir=path.parent, prefix=".last_weekly.", delete=False, encoding="utf-8"
    ) as f:
        f.write(dt.isoformat())
        tmp_name = f.name
    os.replace(tmp_name, path)


def build_summary(log_dir: Path, start: datetime, end: datetime, host: str) -> str | None:
    scalars: dict[str, list[float]] = {m: [] for m in SCALAR_METRICS}
    disks: dict[str, list[float]] = {}

    for fp in _iter_tick_files(log_dir, start, end):
        try:
            with fp.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        ts = datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))
                    except (ValueError, KeyError, TypeError):
                        log.warning("skipping bad tick-log line in %s", fp.name)
                        continue
                    if ts < start or ts >= end:
                        continue
                    for m in scalars:
                        v = rec.get(m)
                        if isinstance(v, (int, float)):
                            scalars[m].append(float(v))
                    for mount, v in (rec.get("disk_used") or {}).items():
                        if isinstance(v, (int, float)):
                            disks.setdefault(mount, []).append(float(v))
        except OSError:
            log.warning("could not read %s", fp)

    if not scalars["cpu_used"] and not disks:
        return None

    lines = [
        f"🐾 weekly summary for `{host}`",
        "",
        f"{'':<8}{'p50':>7}{'p95':>7}{'p99':>7}",
    ]
    for label, key in SCALAR_ROWS:
        samples = scalars[key]
        if not samples:
            continue
        samples.sort()
        p50 = _percentile(samples, 0.50)
        p95 = _percentile(samples, 0.95)
        p99 = _percentile(samples, 0.99)
        lines.append(f"{label:<8}{_fmt(key, p50):>7}{_fmt(key, p95):>7}{_fmt(key, p99):>7}")

    if disks:
        lines.append("mounts")
        for mount in sorted(disks):
            samples = sorted(disks[mount])
            p50 = _percentile(samples, 0.50)
            p95 = _percentile(samples, 0.95)
            p99 = _percentile(samples, 0.99)
            lines.append(f"  {mount:<6}{_fmt_pct(p50):>7}{_fmt_pct(p95):>7}{_fmt_pct(p99):>7}")

    return "\n".join(lines)


def _iter_tick_files(log_dir: Path, start: datetime, end: datetime) -> list[Path]:
    if not log_dir.is_dir():
        return []
    files: list[Path] = []
    active = log_dir / TICK_LOG_PREFIX
    if active.exists():
        files.append(active)
    # Rotated file suffix is the date the file covered; include a 1-day buffer
    # on either side so a tick that landed near a rotation boundary isn't missed.
    low = start.date() - timedelta(days=1)
    high = end.date() + timedelta(days=1)
    for p in log_dir.glob(f"{TICK_LOG_PREFIX}.*"):
        suffix = p.name[len(TICK_LOG_PREFIX) + 1 :]
        try:
            d = datetime.strptime(suffix, "%Y-%m-%d").date()
        except ValueError:
            continue
        if low <= d <= high:
            files.append(p)
    return files


def _percentile(sorted_samples: list[float], p: float) -> float:
    n = len(sorted_samples)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_samples[0]
    k = (n - 1) * p
    lo = int(k)
    hi = min(lo + 1, n - 1)
    frac = k - lo
    return sorted_samples[lo] * (1 - frac) + sorted_samples[hi] * frac


def _fmt(metric: str, value: float) -> str:
    if metric == "load_per_core":
        return f"{value:.2f}"
    return _fmt_pct(value)


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"
