"""QThread-based SNMP workers. Keep the Qt event loop free while pysnmp calls block."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from . import snmp_ops


class SnmpWorker(QObject):
    """Runs one SNMP op. Emits `finished` with results or `failed` with a string."""
    progress = pyqtSignal(object)   # VarBind per GETNEXT (walk only)
    finished = pyqtSignal(list)     # list[VarBind]
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[..., Any], *args, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            if self._fn is snmp_ops.op_walk:
                def _cb(vb):
                    if self._cancel:
                        raise snmp_ops.SnmpError("cancelled")
                    self.progress.emit(vb)
                result = self._fn(*self._args, cb=_cb)
            else:
                result = self._fn(*self._args, **self._kwargs)
            self.finished.emit(list(result))
        except Exception as exc:
            self.failed.emit(str(exc))


def run_op(parent: QObject, fn: Callable[..., Any],
           on_finished: Callable[[list], None],
           on_failed: Callable[[str], None],
           on_progress: Callable[[Any], None] | None = None,
           *args, **kwargs) -> tuple[QThread, SnmpWorker]:
    """Wire a worker onto a new thread. Returns (thread, worker). Caller keeps
    references."""
    thread = QThread(parent)
    worker = SnmpWorker(fn, *args, **kwargs)
    # Stash the worker on the thread. Without a Python reference (or a Qt
    # parent-child link) a bare QObject.Worker is eligible for garbage
    # collection the moment this function returns, even though thread.started
    # is connected to its .run method. The connection goes stale and the
    # thread never gets work to do.
    thread._worker_ref = worker   # type: ignore[attr-defined]
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(on_finished)
    worker.failed.connect(on_failed)
    if on_progress is not None:
        worker.progress.connect(on_progress)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread, worker


def prune_threads(pool: list) -> None:
    """Drop refs to QThread objects that have already finished.

    Callers keep a list so GC doesn't collect the thread while it's
    still running; but once a thread is done, pinning a reference to
    its now-`deleteLater()`-ed skeleton just leaks memory and makes
    closeEvent's wait-loop iterate over stale entries. Call this at
    the top of any periodic refresh that spawns workers."""
    from PyQt6.sip import isdeleted
    alive = []
    for t in pool:
        try:
            if isdeleted(t):
                continue
            if not t.isRunning():
                continue
            alive.append(t)
        except RuntimeError:
            # Qt already destroyed the underlying C++ object.
            continue
    pool[:] = alive
