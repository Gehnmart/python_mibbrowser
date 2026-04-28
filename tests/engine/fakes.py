"""Pure fakes for engine tests. Zero library deps — these implement the
engine ports using only stdlib types so tests are deterministic and fast.

Each fake holds its own state (no globals) and exposes a recording
interface for assertions."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pymibbrowser.engine.model import Agent, VarBind

# --- Clock ---------------------------------------------------------------

@dataclass
class FakeClock:
    """Records every sleep into ``sleeps`` and advances ``elapsed`` instead
    of actually blocking. ``now()`` reads ``elapsed``."""
    elapsed: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds

    def now(self) -> float:
        return self.elapsed


# --- SnmpTransport -------------------------------------------------------

@dataclass
class _Call:
    host: str
    port: int
    arg: Any


class RecordingSnmp:
    """Configurable SnmpTransport. Each method is backed by a callable
    the test sets — by default returns []. Calls land in .get_calls etc."""

    def __init__(self,
                 get_fn: Callable[[Agent, list], list[VarBind]] | None = None,
                 next_fn: Callable[[Agent, list], list[VarBind]] | None = None,
                 set_fn: Callable[[Agent, list], list[VarBind]] | None = None) -> None:
        self.get_calls: list[_Call] = []
        self.next_calls: list[_Call] = []
        self.set_calls: list[_Call] = []
        self._get = get_fn or (lambda _a, _o: [])
        self._next = next_fn or (lambda _a, _o: [])
        self._set = set_fn or (lambda _a, _p: [])

    def get(self, agent: Agent, oids: list[tuple[int, ...]]) -> list[VarBind]:
        self.get_calls.append(_Call(agent.host, agent.port, list(oids)))
        return self._get(agent, oids)

    def get_next(self, agent: Agent, oids: list[tuple[int, ...]]) -> list[VarBind]:
        self.next_calls.append(_Call(agent.host, agent.port, list(oids)))
        return self._next(agent, oids)

    def set(self, agent: Agent,
             pairs: list[tuple[tuple[int, ...], str, str]]) -> list[VarBind]:
        self.set_calls.append(_Call(agent.host, agent.port, list(pairs)))
        return self._set(agent, pairs)


# --- Resolver ------------------------------------------------------------

class DictResolver:
    """Resolver backed by a name → tuple table. Anything purely numeric
    (with optional leading dot) is parsed straight to a tuple."""

    def __init__(self, table: dict[str, tuple[int, ...]] | None = None) -> None:
        self.table = dict(table or {})

    def resolve(self, name_or_oid: str) -> tuple[int, ...] | None:
        s = name_or_oid.strip().lstrip(".")
        if not s:
            return None
        if s in self.table:
            return self.table[s]
        if all(p.isdigit() for p in s.split(".")):
            return tuple(int(p) for p in s.split("."))
        # name[.suffix] — support sysUpTime.0 etc.
        head, *tail = s.split(".")
        if head in self.table and all(p.isdigit() for p in tail):
            return self.table[head] + tuple(int(p) for p in tail)
        return None


# --- Logger --------------------------------------------------------------

class ListLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, message: str) -> None:
        self.lines.append(message)


# --- OutputSink ----------------------------------------------------------

class ListSink:
    """Sink that buffers lines per-target and produces an in-memory record
    of what would have been persisted. closed_with maps target → list[lines]."""

    def __init__(self) -> None:
        self.target: str | None = None
        self.buf: list[str] = []
        self.closed_with: list[tuple[str, tuple[str, ...]]] = []

    def open(self, target: str) -> None:
        # Flush previous capture if any (matches the "later open replaces" idea).
        if self.target is not None:
            self.closed_with.append((self.target, tuple(self.buf)))
        self.target = target
        self.buf = []

    def emit(self, line: str) -> None:
        if self.target is not None:
            self.buf.append(line)

    def close(self) -> None:
        if self.target is not None:
            self.closed_with.append((self.target, tuple(self.buf)))
            self.target = None
            self.buf = []
