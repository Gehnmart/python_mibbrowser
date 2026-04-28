"""OutputSink adapters — engine.ports.OutputSink implementations.

FileSink writes the buffered lines to a path on close(); NullSink
discards everything. Both hold their own state, no globals."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


class NullSink:
    """No-op sink. Use when the caller doesn't want save-to-file
    semantics — the engine still calls open/emit/close, the adapter
    just drops everything on the floor."""

    def open(self, target: str) -> None:
        pass

    def emit(self, line: str) -> None:
        pass

    def close(self) -> None:
        pass


class FileSink:
    """Buffers emit() calls into memory; on close(), writes them to
    `target` chosen via the script's `save` command. If the chosen path
    already exists, appends `.1`, `.2`, ... until a free slot is found —
    we never overwrite an existing capture file.

    A callback fires with the final path written so the engine's logger
    can announce 'saved N lines to /tmp/out.txt' the way the original
    script_runner did."""

    def __init__(self, on_persist: Callable[[Path, int], None] | None = None) -> None:
        self._target: Path | None = None
        self._buffer: list[str] = []
        self._on_persist = on_persist

    def open(self, target: str) -> None:
        # If the script issues `save A` then `save B`, A is dropped on
        # the floor — matches the original behaviour where save_path was
        # plainly overwritten.
        self._target = Path(target).expanduser()
        self._buffer = []

    def emit(self, line: str) -> None:
        if self._target is not None:
            self._buffer.append(line)

    def close(self) -> None:
        if self._target is None or not self._buffer:
            self._target = None
            self._buffer = []
            return
        path = self._target
        i = 0
        while path.exists():
            i += 1
            path = self._target.with_suffix(self._target.suffix + f".{i}")
        # `save subdir/out.txt` should not crash if subdir doesn't yet
        # exist — match JsonFileSettingsStore.save's "create parent on
        # write" behaviour.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self._buffer), encoding="utf-8")
        if self._on_persist is not None:
            self._on_persist(path, len(self._buffer))
        self._target = None
        self._buffer = []
