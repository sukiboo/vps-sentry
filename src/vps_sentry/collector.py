from __future__ import annotations

import logging
from datetime import datetime, timezone

import psutil

from .models import ProcInfo, Snapshot

log = logging.getLogger(__name__)

CPU_SAMPLE_INTERVAL = 1.0


def collect(mounts: list[str]) -> Snapshot:
    load_1, load_5, load_15 = psutil.getloadavg()
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    cpu_times = psutil.cpu_times_percent(interval=None)

    disk_used: dict[str, float] = {}
    for mount in mounts:
        try:
            disk_used[mount] = psutil.disk_usage(mount).percent
        except (FileNotFoundError, PermissionError, OSError) as exc:
            log.warning("disk_usage(%s) failed: %s", mount, exc)

    return Snapshot(
        ts=datetime.now(timezone.utc),
        load_1=load_1,
        load_5=load_5,
        load_15=load_15,
        cpu_count=psutil.cpu_count(logical=True) or 1,
        cpu_used=psutil.cpu_percent(interval=None),
        mem_used=vm.percent,
        swap_used=sw.percent,
        disk_used=disk_used,
        iowait=getattr(cpu_times, "iowait", 0.0),
        proc_count=len(psutil.pids()),
    )


def top_processes(n: int = 5) -> tuple[list[ProcInfo], list[ProcInfo]]:
    procs: list[ProcInfo] = []
    # Prime cpu_percent on each proc; first call returns 0.0.
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            p.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Short sample window so top-CPU rankings are meaningful.
    psutil.cpu_percent(interval=CPU_SAMPLE_INTERVAL)

    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            info = p.info
            mem = info.get("memory_info")
            procs.append(
                ProcInfo(
                    pid=info["pid"],
                    name=info.get("name") or "",
                    cmdline=" ".join(info.get("cmdline") or []) or (info.get("name") or ""),
                    cpu_pct=p.cpu_percent(interval=None),
                    rss_bytes=mem.rss if mem else 0,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    by_cpu = sorted(procs, key=lambda x: x.cpu_pct, reverse=True)[:n]
    by_mem = sorted(procs, key=lambda x: x.rss_bytes, reverse=True)[:n]
    return by_cpu, by_mem
