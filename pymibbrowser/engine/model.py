"""Pure data types shared between the engine and its adapters.

No library dependencies — no pysnmp, no Qt, no filesystem. Adapters import
these to type their boundary; the engine consumes them without ever
materialising vendor-specific value objects.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Agent:
    """SNMP connection parameters. Pure data — adapters interpret each
    field per their concrete transport."""

    host: str = "127.0.0.1"
    port: int = 161
    version: str = "2c"             # "1" | "2c" | "3"
    read_community: str = "public"
    write_community: str = "private"
    timeout_s: float = 3.0
    retries: int = 1
    max_repetitions: int = 10
    non_repeaters: int = 0
    user: str = ""
    auth_protocol: str = "none"
    auth_password: str = ""
    priv_protocol: str = "none"
    priv_password: str = ""


@dataclass(frozen=True)
class VarBind:
    """One result triple from an SNMP transport. Pure values; the
    pysnmp-side rfc1902 object never crosses the engine boundary."""

    oid: tuple[int, ...]
    type_name: str          # transport-defined label (e.g. "TimeTicks")
    display_value: str      # human-readable string the engine prints


@dataclass(frozen=True)
class TrapEvent:
    """One decoded SNMP trap. Pure data — no rfc1902 / pyasn1 internals.

    Adapters parse incoming UDP datagrams and produce these; consumers
    (UI tabs, CLI listeners, log writers) read them. The two SNMPv1-only
    fields (enterprise / generic_trap / specific_trap / agent_addr) are
    empty for v2c traps — a single struct keeps the consumer code
    branch-free."""

    received_at: float                  # seconds since epoch
    source_ip: str
    source_port: int
    version: str                        # "1" | "2c"
    community: str
    trap_oid: str
    uptime: int = 0
    enterprise: str = ""                # v1 only
    generic_trap: int = 0               # v1 only
    specific_trap: int = 0              # v1 only
    agent_addr: str = ""                # v1 only
    # (oid, type_name, display_value) per varbind — same shape as VarBind
    # but plain tuples to keep TrapEvent hashable.
    var_binds: tuple[tuple[str, str, str], ...] = ()
    raw_bytes: bytes = b""


@dataclass(frozen=True)
class MibNodeView:
    """Read-only view of a MIB node — what callers need to render or
    inspect a node without importing infra types. Adapters materialise
    this from their concrete representation (e.g. infra.MibNode)."""

    name: str
    oid: tuple[int, ...]
    module: str = ""
    syntax: str = ""              # SMI type name (Integer32, OCTET STRING, ...)
    access: str = ""              # read-only / read-write / ...
    description: str = ""
    units: str = ""
    indices: tuple[str, ...] = ()
    enum_values: tuple[tuple[int, str], ...] = ()
