"""Engine ports — Protocols every adapter must implement.

The engine never imports concrete implementations; it talks to these
interfaces only. Adapters wire up the real world (pysnmp, sockets, the
filesystem, the system clock) on the other side. Hard interfaces, no
shared state, no globals — each adapter holds its own state.
"""
from __future__ import annotations

from typing import Protocol

from .model import Agent, VarBind


class SnmpTransport(Protocol):
    """Synchronous SNMP transport. Engine's view of "the network"."""

    def get(self, agent: Agent,
             oids: list[tuple[int, ...]]) -> list[VarBind]: ...

    def get_next(self, agent: Agent,
                  oids: list[tuple[int, ...]]) -> list[VarBind]: ...

    def set(self, agent: Agent,
             pairs: list[tuple[tuple[int, ...], str, str]]) -> list[VarBind]:
        """pairs: (oid, type_tag, raw_value). Adapter encodes the value
        per its concrete transport (e.g. pysnmp.rfc1902 for SNMP)."""


class Clock(Protocol):
    """Time abstraction — replace with a fake for deterministic tests."""

    def sleep(self, seconds: float) -> None: ...

    def now(self) -> float: ...


class Resolver(Protocol):
    """Symbolic-name → numeric-OID lookup."""

    def resolve(self, name_or_oid: str) -> tuple[int, ...] | None: ...


class Logger(Protocol):
    """Diagnostic / trace messages. No formatting expectations."""

    def log(self, message: str) -> None: ...


class OutputSink(Protocol):
    """Result-line stream with optional save-to-target buffering.

    The engine calls open(target) when it sees a `save` command; from that
    point until close(), every emit() is buffered. close() persists the
    buffer (typically by writing it somewhere — that's the adapter's call).
    Before any open(), emit() is allowed to be a no-op."""

    def open(self, target: str) -> None: ...

    def emit(self, line: str) -> None: ...

    def close(self) -> None: ...
