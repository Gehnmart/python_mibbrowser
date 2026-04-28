"""Defensive helpers around the QThread lifecycle.

Qt's standard teardown chain is

    thread.finished.connect(thread.deleteLater)

which means the underlying C++ QThread is deleted as soon as the
event loop processes the next round, while the Python sip proxy
remains. Calling any method on the proxy after that raises

    RuntimeError: wrapped C/C++ object of type QThread has been deleted

This module wraps every "is the thread still alive?" / "wait on it" /
"clean up the pool" pattern with the same defensive scaffolding so we
have one canonical implementation. UI code calls these instead of
poking ``thread.isRunning()`` directly.
"""
from __future__ import annotations

import time


def is_thread_alive(t) -> bool:
    """True iff ``t`` is a still-alive QThread that's currently running.

    None / sip-deleted / "between deleteLater and the next event loop
    tick" all count as not-alive — these are the cases where calling
    ``isRunning()`` would raise. The whole chain is wrapped so a race
    where the C++ side disappears between the isdeleted() check and
    the isRunning() call also degrades to False rather than crashing.

    A TypeError from ``isdeleted`` (i.e. ``t`` is not a sip-wrapped
    object — convenient for tests) is treated as "not a Qt object" and
    we fall through to ``isRunning()`` directly."""
    if t is None:
        return False
    try:
        from PyQt6.sip import isdeleted
        if isdeleted(t):
            return False
    except TypeError:
        pass
    try:
        return bool(t.isRunning())
    except (RuntimeError, TypeError):
        return False


def wait_if_running(thread, ms: int = 800) -> None:
    """Block up to ``ms`` ms for ``thread`` to finish, surviving the
    race where Qt has already deleted the underlying C++ object since
    we last saw it running."""
    if not is_thread_alive(thread):
        return
    try:
        thread.wait(ms)
    except (RuntimeError, TypeError):
        # C++ side disappeared between the alive-check and wait().
        pass


def prune_threads(pool: list) -> None:
    """Drop refs to QThread objects that have already finished.

    Callers keep a list so GC doesn't collect a thread while it's
    still running; once a thread is done, pinning its now-deleteLater'd
    skeleton just leaks memory and makes shutdown loops iterate over
    stale entries. Call this at the top of any periodic refresh that
    spawns workers."""
    pool[:] = [t for t in pool if is_thread_alive(t)]


def shutdown_pools(pools: list[list], total_ms: int = 500) -> None:
    """Fast shutdown of several thread pools.

    Why not just call wait_if_running on each thread? Because that
    serialises: 9 Port-View walks × 500 ms each = 4.5 s felt by the
    user on app close. Instead:

      1. Flip ``_cancel`` on every worker (op_walk notices next
         iteration; op_get/op_next are stuck in pysnmp's blocking
         socket but will emit failed→quit as soon as the UDP timeout
         expires).
      2. Call requestInterruption() on each thread — pysnmp ignores
         it, but custom workers may check it.
      3. Poll at 50 ms intervals for ``total_ms`` total. Threads that
         finish early free us to close faster; stragglers are detached.

    Detached QThreads finish in the background. ``thread.finished``
    handlers use deleteLater so they tear themselves down cleanly even
    after the parent window has gone — Qt auto-disconnects signal
    targets that no longer exist."""
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
    deadline = time.monotonic() + total_ms / 1000.0
    step = 0.05   # 50 ms
    while time.monotonic() < deadline:
        still_running = False
        for t in threads:
            if is_thread_alive(t):
                still_running = True
                break
        if not still_running:
            return
        time.sleep(step)
