from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from .models import Snapshot, Tier


@dataclass
class AlertState:
    history: deque[Snapshot] = field(default_factory=lambda: deque(maxlen=100))
    cooldowns: dict[tuple[str, str | None, Tier], datetime] = field(default_factory=dict)
    # metric+mount -> currently-active tier, to drive recovery alerts.
    active: dict[tuple[str, str | None], Tier] = field(default_factory=dict)

    def configure_history(self, sustained_checks: int) -> None:
        maxlen = max(100, sustained_checks * 3)
        if self.history.maxlen != maxlen:
            self.history = deque(self.history, maxlen=maxlen)
