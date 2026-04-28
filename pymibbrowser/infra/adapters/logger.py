"""Logger adapters — engine.ports.Logger implementations."""
from __future__ import annotations

from collections.abc import Callable


class CallbackLogger:
    """Forwards every line to a caller-supplied callable. The UI passes
    its `append-to-log-pane` slot here."""

    def __init__(self, sink: Callable[[str], None]) -> None:
        self._sink = sink

    def log(self, message: str) -> None:
        self._sink(message)


class PrintLogger:
    """Writes to stdout. Used by the CLI / when no logger is provided."""

    def log(self, message: str) -> None:
        print(message)
