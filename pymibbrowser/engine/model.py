"""Pure data types shared between the engine and its adapters.

No library dependencies — no pysnmp, no Qt, no filesystem. Adapters import
these to type their boundary; the engine consumes them without ever
materialising vendor-specific value objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass
class WatchDefinition:
    """A single monitored OID with a 'normal-state' predicate. Pure data;
    UI evaluates the predicate against incoming values."""
    name: str = ""
    oid: str = ""
    operation: str = "Get"              # "Get" | "Get Next"
    condition_op: str = ">"
    condition_value: str = ""           # string; parsed as float if possible


@dataclass
class PollVariable:
    """One variable inside a Poll definition."""
    name: str = ""                      # display label, e.g. "sysUpTime"
    oid: str = ""                       # dotted numeric or symbolic
    operation: str = "Get"              # "Get" | "Get Next"


@dataclass
class PollDefinition:
    """Periodic poll across one or more agents for a set of variables.
    ``agents`` stores the agent identifier as ``host:port``; resolved
    against AppSettings.saved_agents + current_agent at run time."""
    name: str = ""
    interval_s: int = 30
    agents: list[str] = field(default_factory=list)
    variables: list[PollVariable] = field(default_factory=list)


@dataclass
class AppSettings:
    """All persistent application state. Pure data — persistence (load/
    save, JSON serialisation, file paths) lives in a SettingsStore
    adapter; nothing on this class touches the filesystem. Callers
    persist via ``infra.config.save_settings(s)`` /
    ``infra.config.load_settings()`` (or a SettingsStore directly)."""

    current_agent: Agent = field(default_factory=Agent)
    # Template used as the starting point for new agents (Add in Manage
    # agents, and the toolbar's Address combo on first use). Edited via
    # Preferences → SNMP. Keeping it separate from current_agent means
    # changing "defaults" doesn't rewrite the agent you're actively
    # talking to.
    default_agent: Agent = field(default_factory=Agent)
    saved_agents: list[Agent] = field(default_factory=list)
    loaded_mibs: list[str] = field(default_factory=list)
    bookmarks: list[dict] = field(default_factory=list)
    trap_port: int = 162
    max_graph_points: int = 600
    single_tree_root: bool = True
    show_log_pane: bool = True
    lenient_mib_parser: bool = True
    logging_level: str = "INFO"
    log_dir: str = ""    # "" = default under data_dir()/logs
    language: str = ""   # "" = auto-detect from $LANG; "ru" | "en" to pin
    # Explicit enable-list. None (stored as null) = all compiled MIBs are
    # loaded into the tree. A list (possibly empty) narrows the tree to
    # just those modules.
    enabled_mibs: list[str] | None = None
    # When compiling MIBs, fall back to https://mibs.pysnmp.com for any
    # module not in the local source dirs.
    fetch_missing_from_net: bool = False
    polls: list[PollDefinition] = field(default_factory=list)
    watches: list[WatchDefinition] = field(default_factory=list)
    watch_interval_s: int = 15
    # Comma-separated host/CIDR allow-list for the trap receiver; empty
    # accepts any source.
    trap_accept_from: str = ""
    # MRU list of OIDs the user actually ran. Top is most recent;
    # capped at 20 entries.
    recent_oids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CompileResult:
    """Outcome of compiling a single MIB module. ``status`` is the raw
    pysmi status string ("compiled", "untouched", or "failed: <reason>")
    — adapters may surface other strings; consumers should treat
    anything not starting with "failed" as success."""

    module: str
    status: str

    @property
    def ok(self) -> bool:
        return not self.status.startswith("failed")


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
