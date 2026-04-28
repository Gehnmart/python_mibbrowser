"""Engine ports — Protocols every adapter must implement.

The engine never imports concrete implementations; it talks to these
interfaces only. Adapters wire up the real world (pysnmp, sockets, the
filesystem, the system clock) on the other side. Hard interfaces, no
shared state, no globals — each adapter holds its own state.
"""
from __future__ import annotations

from typing import Protocol

from collections.abc import Callable

from .model import Agent, MibNodeView, TrapEvent, VarBind


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


class TrapPublisher(Protocol):
    """Send SNMP traps. The boundary speaks (oid, type_tag, raw) triples
    — same shape as ``SnmpTransport.set`` — so the adapter owns the
    vendor-encoding step and the engine never sees rfc1902."""

    def send(self, host: str, port: int, community: str, version: str,
              trap_oid: str,
              var_binds: list[tuple[str, str, str]]) -> str:
        """Returns "OK" on success, "error: <ind>" if pysnmp surfaced an
        error indication, "status: <stat>" if the agent answered with an
        SNMP error status."""


class TrapSubscription(Protocol):
    """Subscription to incoming SNMP traps. The on-trap callback is
    captured at construction; start()/stop() control the listener
    lifecycle."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def is_running(self) -> bool: ...


class MibStore(Protocol):
    """Application-level view of the MIB catalogue.

    Lets non-UI consumers (CLI, web frontend, scripts) work with MIB
    modules without importing infra types. The resolver() return value
    is the engine's ``Resolver`` — same Protocol, freshly bound to the
    current tree state, so it stays valid across set_enabled() rebuilds."""

    def resolver(self) -> "Resolver": ...

    def available_modules(self) -> list[str]:
        """Every module the store knows about, whether enabled or not.
        Sorted, no duplicates."""

    def enabled_modules(self) -> list[str]:
        """Module names currently merged into the resolver's tree.
        Subset of available_modules(). Sorted."""

    def set_enabled(self, modules: list[str]) -> None:
        """Re-merge the tree with only the given modules. Modules not in
        ``available_modules()`` are silently ignored — same forgiving
        contract as the underlying loader."""

    def find_node(self, oid: tuple[int, ...]) -> MibNodeView | None:
        """Nearest named ancestor for a numeric OID, as a read-only view.
        Returns None only if the OID is shorter than any known node
        (i.e. nothing in the tree at all)."""
