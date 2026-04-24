"""App-wide configuration: paths, persistence of agents/bookmarks/settings."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


APP_NAME = "pymibbrowser"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def compiled_mibs_dir() -> Path:
    p = data_dir() / "compiled_mibs"
    p.mkdir(parents=True, exist_ok=True)
    return p


_log_dir_override: Optional[Path] = None


def set_log_dir_override(path: Optional[str]) -> None:
    """main() calls this once after reading settings — lets the UI's
    'Change log directory' take effect without plumbing settings through
    every callsite of log_file()."""
    global _log_dir_override
    _log_dir_override = Path(path).expanduser() if path else None


def log_dir() -> Path:
    if _log_dir_override is not None:
        _log_dir_override.mkdir(parents=True, exist_ok=True)
        return _log_dir_override
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_file() -> Path:
    return log_dir() / "pymibbrowser.log"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_mibs_src() -> Path:
    return project_root() / "mibs-src"


@dataclass
class Agent:
    host: str = "127.0.0.1"
    port: int = 161
    version: str = "2c"               # "1" | "2c" | "3"
    read_community: str = "public"
    write_community: str = "private"
    timeout_s: float = 3.0
    retries: int = 1
    max_repetitions: int = 10
    non_repeaters: int = 0
    # SNMPv3 (placeholder — не реализовано, пользователь попросил без v3)
    user: str = ""
    auth_protocol: str = "none"
    auth_password: str = ""
    priv_protocol: str = "none"
    priv_password: str = ""


@dataclass
class WatchDefinition:
    """A single monitored OID with a 'normal-state' predicate.
    `condition_op` is one of '>', '<', '>=', '<=', '==', '!='. The Watch
    tab colours the row green when the condition holds, red when it
    doesn't, grey while the value is pending."""
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
    """Periodic poll across one or more agents for a set of variables
    — matches iReasoning's Polls feature. Serialises to JSON via asdict.

    `agents` stores the agent identifier as "host:port"; resolved against
    AppSettings.saved_agents + current_agent at run time."""
    name: str = ""
    interval_s: int = 30
    agents: list[str] = field(default_factory=list)
    variables: list[PollVariable] = field(default_factory=list)


@dataclass
class AppSettings:
    current_agent: Agent = field(default_factory=Agent)
    # Template used as the starting point for new agents (Add in Manage
    # agents, and the toolbar's Address combo on first use). Edited via
    # Preferences → SNMP. Keeping it separate from current_agent means
    # changing "defaults" doesn't rewrite the agent you're actively
    # talking to.
    default_agent: Agent = field(default_factory=Agent)
    saved_agents: list[Agent] = field(default_factory=list)
    loaded_mibs: list[str] = field(default_factory=list)
    bookmarks: list[dict] = field(default_factory=list)   # {name, oid, operation, host}
    trap_port: int = 162
    max_graph_points: int = 600
    single_tree_root: bool = True
    show_log_pane: bool = True
    lenient_mib_parser: bool = True
    logging_level: str = "INFO"
    log_dir: str = ""    # "" = default under data_dir()/logs
    language: str = ""   # "" = auto-detect from $LANG; "ru" | "en" to pin
    # Explicit enable-list. None (stored as null) = all compiled MIBs are
    # loaded into the tree. A list (possibly empty) narrows the tree to just
    # those modules — useful when the user has a vendor MIB and doesn't want
    # the standard SMI/SNMPv2 noise alongside it.
    enabled_mibs: Optional[list[str]] = None
    # When compiling MIBs, should we fall back to https://mibs.pysnmp.com
    # for modules that aren't in mibs-src/ (or any extra source dir the
    # user added)? Off by default — predictable offline behaviour; users
    # turn it on explicitly via the Rebuild dialog or the Load MIB
    # dialog's checkbox.
    fetch_missing_from_net: bool = False
    polls: list[PollDefinition] = field(default_factory=list)
    watches: list[WatchDefinition] = field(default_factory=list)
    watch_interval_s: int = 15
    # Accept-list for the Trap Receiver. Empty string = accept any
    # source (current default). Otherwise a comma-separated list of
    # hosts / CIDRs: "10.0.0.0/8, 192.168.1.5". Non-matching datagrams
    # are dropped before parsing — DoS-resistant.
    trap_accept_from: str = ""
    # MRU list of OIDs the user actually ran — populated on each GET/
    # WALK/SET. Top of the list is most recent. Limited to 20 entries.
    recent_oids: list[str] = field(default_factory=list)

    # Custom reconstructors for dataclass-typed fields. Plain fields
    # (str/int/bool/list[str]/dict) are handled generically below.
    _NESTED_LOADERS = {
        "current_agent": lambda v: Agent(**(v or {})),
        "default_agent": lambda v: Agent(**(v or {})),
        "saved_agents":  lambda v: [Agent(**a) for a in (v or [])
                                     if isinstance(a, dict)],
        "polls":         lambda v: [
            PollDefinition(
                name=p.get("name", ""),
                interval_s=int(p.get("interval_s", 30) or 30),
                agents=list(p.get("agents", [])),
                variables=[PollVariable(**x)
                           for x in p.get("variables", [])
                           if isinstance(x, dict)],
            ) for p in (v or []) if isinstance(p, dict)
        ],
        "watches":       lambda v: [WatchDefinition(**w)
                                     for w in (v or [])
                                     if isinstance(w, dict)],
    }

    @classmethod
    def load(cls) -> "AppSettings":
        """Field-driven loader. Adding a new simple field to this
        dataclass — int/str/bool/list/dict/Optional — Just Works with
        no change here. Nested dataclasses register in _NESTED_LOADERS."""
        path = config_dir() / "settings.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except Exception:
            return cls()
        import dataclasses
        kwargs = {}
        for f in dataclasses.fields(cls):
            if f.name not in data:
                continue
            raw = data[f.name]
            loader = cls._NESTED_LOADERS.get(f.name)
            if loader is not None:
                try:
                    kwargs[f.name] = loader(raw)
                except Exception:
                    continue
            else:
                kwargs[f.name] = raw
        return cls(**kwargs)

    def save(self) -> None:
        """Atomic: write to a temp file next to settings.json and rename.
        A crash mid-write can no longer leave an empty / truncated
        settings file (which would wipe bookmarks, saved agents, etc.)."""
        path = config_dir() / "settings.json"
        # asdict walks nested dataclasses recursively — one call covers
        # current_agent, default_agent, each Agent in saved_agents, each
        # PollDefinition/PollVariable, each WatchDefinition, and all
        # the plain fields. No more per-field enumeration to maintain.
        # Private-by-convention fields (anything starting with `_`) are
        # class-level config like _NESTED_LOADERS; drop them from the
        # serialised output.
        data = asdict(self)
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        payload = json.dumps(data, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(payload)
        tmp.replace(path)    # os.rename is atomic on POSIX within the same dir
