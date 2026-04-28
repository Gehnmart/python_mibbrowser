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
