"""Clock adapter — engine.ports.Clock backed by the standard library."""
from __future__ import annotations

import time


class WallClock:
    """Real time. The engine uses this in production; tests inject a
    FakeClock that records sleeps without blocking."""

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        return time.monotonic()
