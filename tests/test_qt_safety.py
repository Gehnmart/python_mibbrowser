"""Defensive helpers in pymibbrowser.qt_safety. The actual Qt wiring
is exercised by UI smoke; here we pin the contracts: None → False,
deleted-proxy → False, exceptions get swallowed."""
from __future__ import annotations

from pymibbrowser import qt_safety, workers

# --- is_thread_alive ------------------------------------------------------

def test_alive_none_is_false():
    assert qt_safety.is_thread_alive(None) is False


class _DeletedProxy:
    """Mimics a sip-deleted QThread: isRunning() raises like the real
    runtime does once the C++ object is gone."""
    def isRunning(self):
        raise RuntimeError("wrapped C/C++ object has been deleted")


def test_alive_swallows_runtime_error():
    assert qt_safety.is_thread_alive(_DeletedProxy()) is False


class _LiveStub:
    def __init__(self, running: bool):
        self._running = running
    def isRunning(self):
        return self._running


def test_alive_returns_isrunning_for_live_thread():
    assert qt_safety.is_thread_alive(_LiveStub(True)) is True
    assert qt_safety.is_thread_alive(_LiveStub(False)) is False


# --- wait_if_running ------------------------------------------------------

def test_wait_if_running_noop_on_none():
    qt_safety.wait_if_running(None)         # must not raise


def test_wait_if_running_noop_on_dead():
    qt_safety.wait_if_running(_DeletedProxy(), 100)


class _WaitingStub:
    """Tracks wait() calls; reports running=True so wait gets invoked."""
    def __init__(self):
        self.waited: list[int] = []
    def isRunning(self):
        return True
    def wait(self, ms):
        self.waited.append(ms)


def test_wait_if_running_calls_wait_for_live_thread():
    s = _WaitingStub()
    qt_safety.wait_if_running(s, 250)
    assert s.waited == [250]


# --- prune_threads --------------------------------------------------------

def test_prune_drops_dead_keeps_live():
    live = _LiveStub(True)
    dead = _DeletedProxy()
    finished = _LiveStub(False)
    pool = [live, dead, finished, None]
    qt_safety.prune_threads(pool)
    assert pool == [live]


def test_prune_empty_pool():
    pool: list = []
    qt_safety.prune_threads(pool)
    assert pool == []


# --- workers re-exports ---------------------------------------------------

def test_workers_re_exports_qt_safety_helpers():
    """UI imports `from .. import workers; workers.wait_if_running(...)`
    pervasively. After moving the helpers to qt_safety, the workers
    module must still expose them by the same name — verify identity."""
    assert workers.is_thread_alive is qt_safety.is_thread_alive
    assert workers.wait_if_running is qt_safety.wait_if_running
    assert workers.prune_threads is qt_safety.prune_threads
    assert workers.shutdown_pools is qt_safety.shutdown_pools
