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


def log_dir() -> Path:
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
class AppSettings:
    current_agent: Agent = field(default_factory=Agent)
    saved_agents: list[Agent] = field(default_factory=list)
    loaded_mibs: list[str] = field(default_factory=list)
    bookmarks: list[dict] = field(default_factory=list)   # {name, oid, operation, host}
    trap_port: int = 162
    max_graph_points: int = 600
    single_tree_root: bool = True
    lenient_mib_parser: bool = True
    logging_level: str = "INFO"
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

    @classmethod
    def load(cls) -> "AppSettings":
        path = config_dir() / "settings.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except Exception:
            return cls()
        ag = Agent(**data.get("current_agent", {}))
        saved = [Agent(**a) for a in data.get("saved_agents", [])]
        return cls(
            current_agent=ag,
            saved_agents=saved,
            loaded_mibs=data.get("loaded_mibs", []),
            bookmarks=data.get("bookmarks", []),
            trap_port=data.get("trap_port", 162),
            max_graph_points=data.get("max_graph_points", 600),
            single_tree_root=data.get("single_tree_root", True),
            lenient_mib_parser=data.get("lenient_mib_parser", True),
            logging_level=data.get("logging_level", "INFO"),
            language=data.get("language", ""),
            enabled_mibs=data.get("enabled_mibs"),
            fetch_missing_from_net=data.get("fetch_missing_from_net", False),
        )

    def save(self) -> None:
        path = config_dir() / "settings.json"
        data = {
            "current_agent": asdict(self.current_agent),
            "saved_agents": [asdict(a) for a in self.saved_agents],
            "loaded_mibs": self.loaded_mibs,
            "bookmarks": self.bookmarks,
            "trap_port": self.trap_port,
            "max_graph_points": self.max_graph_points,
            "single_tree_root": self.single_tree_root,
            "lenient_mib_parser": self.lenient_mib_parser,
            "logging_level": self.logging_level,
            "language": self.language,
            "enabled_mibs": self.enabled_mibs,
            "fetch_missing_from_net": self.fetch_missing_from_net,
        }
        path.write_text(json.dumps(data, indent=2))
