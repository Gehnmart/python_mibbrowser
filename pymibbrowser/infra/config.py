"""App-wide configuration: paths only.

Persistence is handled by ``infra.adapters.settings``; data types live
in ``engine.model``. This module is the path-resolution layer (XDG dirs,
log dir, MIB source paths) plus a re-export of the data types so
existing callers writing ``from pymibbrowser.infra.config import
AppSettings`` keep working.
"""
from __future__ import annotations

import os
from pathlib import Path

# Re-export the pure data types for back-compat. Engine owns the
# definitions; infra owns persistence and paths.
from ..engine.model import (
    Agent,
    AppSettings,
    PollDefinition,
    PollVariable,
    WatchDefinition,
)

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


def log_dir(custom: str | Path | None = None) -> Path:
    """Resolve the log directory. ``custom`` lets callers (typically
    main()) override the platform default when the user has configured a
    different path in settings.log_dir — passed in explicitly instead of
    via a module global, so calls without it always return the same
    deterministic default."""
    if custom:
        p = Path(custom).expanduser()
    else:
        p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_file(custom_dir: str | Path | None = None) -> Path:
    return log_dir(custom_dir) / "pymibbrowser.log"


def project_root() -> Path:
    # infra/config.py → infra/ → pymibbrowser/ → repo root
    return Path(__file__).resolve().parent.parent.parent


def default_mibs_src() -> Path:
    return project_root() / "mibs-src"


__all__ = [
    "APP_NAME",
    "Agent",
    "AppSettings",
    "PollDefinition",
    "PollVariable",
    "WatchDefinition",
    "config_dir",
    "data_dir",
    "compiled_mibs_dir",
    "log_dir",
    "log_file",
    "project_root",
    "default_mibs_src",
]
