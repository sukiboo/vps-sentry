from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Tier = Literal["warn", "critical", "recover"]

# Direction per metric: "high" means value > threshold is a breach;
# "low" means value < threshold is a breach.
METRIC_DIRECTION: dict[str, str] = {
    "load_per_core": "high",
    "memory_used": "high",
    "swap_used": "high",
    "disk_used": "high",
    "iowait": "high",
}


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    name: str
    cmdline: str
    cpu_pct: float
    rss_bytes: int


@dataclass
class Snapshot:
    ts: datetime
    load_1: float
    load_5: float
    load_15: float
    cpu_count: int
    cpu_used: float
    mem_used: float
    swap_used: float
    disk_used: dict[str, float]
    iowait: float
    proc_count: int

    @property
    def load_per_core(self) -> float:
        return self.load_1 / max(1, self.cpu_count)


@dataclass
class Alert:
    metric: str
    tier: Tier
    value: float
    threshold: float
    mount: str | None = None  # set for disk_used alerts
    snapshot: Snapshot | None = None

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.metric, self.mount)


@dataclass
class Config:
    interval_seconds: int
    sustained_checks: int
    cooldown_minutes: int
    show_top_n_proc: int
    thresholds: dict[str, dict[str, float]]
    mounts: list[str]
    telegram_token: str
    telegram_chat_id: str
    host: str
    config_path: str = ""
