from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import notifier, ticklog
from .collector import collect, top_processes
from .config import load_config
from .evaluator import evaluate
from .state import AlertState

log = logging.getLogger("vps_sentry")

DEFAULT_CONFIG = Path.cwd() / "config.yml"

CLOCK_JUMP_FACTOR = 5
SIGTERM_CHECK_INTERVAL = 1.0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="vps_sentry", description="Lightweight VPS resource monitor")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.yml")
    p.add_argument(
        "--dry-run", action="store_true", help="Print alerts instead of sending Telegram messages"
    )
    p.add_argument("--once", action="store_true", help="Collect one snapshot, print it, exit")
    return p.parse_args(argv)


def _snapshot_to_json(snap) -> str:
    d = asdict(snap)
    d["ts"] = snap.ts.isoformat()
    return json.dumps(d, indent=2, default=str)


def run_once(cfg_path: str) -> int:
    cfg = load_config(cfg_path)
    snap = collect(cfg.mounts)
    print(_snapshot_to_json(snap))
    by_cpu, by_mem = top_processes(cfg.show_top_n_proc)
    cpu_count = max(1, snap.cpu_count)
    print("\nTop by CPU:")
    for p in by_cpu:
        print(f"  {p.cpu_pct / cpu_count:5.1f}%  {p.cmdline or p.name}")
    print("\nTop by RAM:")
    for p in by_mem:
        mb = p.rss_bytes / (1024**2)
        print(f"  {mb:7.1f} MB  {p.cmdline or p.name}")
    return 0


def run_loop(cfg_path: str, dry_run: bool) -> int:
    cfg = load_config(cfg_path)
    state = AlertState()
    state.configure_history(cfg.sustained_checks)
    tick_log = ticklog.setup(Path.cwd() / "logs")

    stop = {"now": False}

    def _handle_sigterm(signum, frame):
        log.info("received signal %s, shutting down", signum)
        stop["now"] = True

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    log.info(
        "vps-sentry starting on %s (interval=%ss, sustained=%s, cooldown=%smin, mounts=%s, dry_run=%s)",
        cfg.host,
        cfg.interval_seconds,
        cfg.sustained_checks,
        cfg.cooldown_minutes,
        cfg.mounts,
        dry_run,
    )
    notifier.send_text(cfg, f"🟢 vps-sentry started on `{cfg.host}`", dry_run=dry_run)

    # Prime cpu_percent so the first real call returns a meaningful value.
    import psutil

    psutil.cpu_percent(interval=None)

    last_tick = time.monotonic()
    while not stop["now"]:
        t0 = time.monotonic()
        wall_gap = t0 - last_tick
        last_tick = t0

        try:
            snap = collect(cfg.mounts)
        except Exception:
            log.exception("collect() failed; skipping tick")
            _sleep_interruptible(cfg.interval_seconds, stop)
            continue

        state.history.append(snap)

        # Clock-jump guard: if we slept much longer than expected (suspend/resume, NTP jump),
        # history is stale — drop it and start over. Leave current snapshot in place.
        if wall_gap > cfg.interval_seconds * CLOCK_JUMP_FACTOR and len(state.history) > 1:
            log.warning("wall-clock gap %.1fs >> interval; resetting history", wall_gap)
            state.history.clear()
            state.history.append(snap)

        try:
            alerts = evaluate(snap, state, cfg)
        except Exception:
            log.exception("evaluate() failed; skipping tick")
            alerts = []

        ticklog.log_tick(tick_log, snap, len(alerts))

        if alerts:
            by_cpu, by_mem = top_processes(cfg.show_top_n_proc)
            for alert in alerts:
                try:
                    notifier.send(cfg, alert, by_cpu, by_mem, dry_run=dry_run)
                except Exception:
                    log.exception("notifier.send failed for alert %s", alert.metric)

        elapsed = time.monotonic() - t0
        remaining = max(0.0, cfg.interval_seconds - elapsed)
        _sleep_interruptible(remaining, stop)

    log.info("vps-sentry exiting")
    notifier.send_text(cfg, f"🔴 vps-sentry stopping on `{cfg.host}`", dry_run=dry_run)
    return 0


def _sleep_interruptible(seconds: float, stop: dict) -> None:
    # Wake up every SIGTERM_CHECK_INTERVAL so SIGTERM is responsive.
    end = time.monotonic() + seconds
    while not stop["now"]:
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(SIGTERM_CHECK_INTERVAL, left))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.once:
        return run_once(args.config)
    return run_loop(args.config, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
