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


def wait_if_running(thread, ms: int = 800) -> None:
    """Block up to `ms` for this QThread to finish — but survive the
    race where Qt has already deleted the underlying C++ object since
    we last saw it running.

    The catch is that QThread's `thread.finished.connect(thread.deleteLater)`
    fires the moment work finishes, which can happen between the time
    we read pool and the time we call isRunning(). Accessing any method
    on the half-dead SIP proxy raises RuntimeError — so wrap the whole
    chain, not just the wait()."""
    from PyQt6.sip import isdeleted
    try:
        if isdeleted(thread):
            return
        if not thread.isRunning():
            return
        thread.wait(ms)
    except (RuntimeError, TypeError):
        # C++ half already collected; nothing to wait on.
        pass


def shutdown_pools(pools: list[list], total_ms: int = 500) -> None:
    """Fast shutdown of several thread pools.

    Why not just call wait_if_running on each thread? Because that
    serialises: 9 Port-View walks × 500 ms each = 4.5 s felt by the
    user on app close. Instead:

      1. Flip `_cancel` on every worker (op_walk notices next iteration,
         op_get/op_next are stuck in pysnmp's blocking socket but will
         emit failed→quit as soon as the UDP timeout expires).
      2. Call requestInterruption on each thread — pysnmp ignores it,
         but custom workers may check it.
      3. Poll at 50 ms intervals for `total_ms` total. Threads that
         finish early free us to close faster; stragglers are detached.

    Detached QThreads finish in the background. Our `thread.finished`
    handlers use deleteLater so they tear themselves down cleanly even
    after the parent window has gone — Qt auto-disconnects signal
    targets that no longer exist."""
    import time

    from PyQt6.sip import isdeleted
    threads: list = []
    for pool in pools:
        for t in list(pool):
            try:
                if isdeleted(t):
                    continue
                # Tell its worker to bail at next opportunity.
                worker = getattr(t, "_worker_ref", None)
                if worker is not None and hasattr(worker, "cancel"):
                    try:
                        worker.cancel()
                    except Exception:
                        pass
                try:
                    t.requestInterruption()
                except Exception:
                    pass
                if t.isRunning():
                    threads.append(t)
            except (RuntimeError, TypeError):
                continue

    if not threads:
        return
    # Poll until either all threads finished or the budget elapses.
    deadline = time.monotonic() + total_ms / 1000.0
    step = 0.05   # 50 ms
    while time.monotonic() < deadline:
        still_running = False
        for t in threads:
            try:
                if not isdeleted(t) and t.isRunning():
                    still_running = True
                    break
            except (RuntimeError, TypeError):
                continue
        if not still_running:
            return
        time.sleep(step)


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
