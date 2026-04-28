"""SettingsStore adapter — engine.ports.SettingsStore over a JSON file.

Owns the polymorphic-field reconstruction map (was AppSettings._NESTED_LOADERS)
and the atomic write sequence (tmp + rename). The dataclass itself stays
pure data in ``engine.model``.
"""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ...engine.model import (
    Agent,
    AppSettings,
    PollDefinition,
    PollVariable,
    WatchDefinition,
)
from ..config import config_dir


# Custom reconstructors for dataclass-typed fields. Plain fields
# (str/int/bool/list[str]/dict) are handled generically below.
_NESTED_LOADERS: dict[str, Callable[[Any], Any]] = {
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


class JsonFileSettingsStore:
    """Read/write AppSettings as a single JSON file. Atomic on POSIX
    (tmp + rename within the same directory)."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self) -> AppSettings:
        """Field-driven loader. Adding a new simple field to AppSettings
        — int/str/bool/list/dict/Optional — Just Works. Nested
        dataclasses register in ``_NESTED_LOADERS``. Returns defaults
        when the file is absent or unreadable so a corrupt persisted
        state can't brick startup."""
        if not self._path.exists():
            return AppSettings()
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            return AppSettings()
        kwargs = {}
        for f in dataclasses.fields(AppSettings):
            if f.name not in data:
                continue
            raw = data[f.name]
            loader = _NESTED_LOADERS.get(f.name)
            if loader is not None:
                try:
                    kwargs[f.name] = loader(raw)
                except Exception:
                    continue
            else:
                kwargs[f.name] = raw
        return AppSettings(**kwargs)

    def save(self, settings: AppSettings) -> None:
        """Atomic: write to a tmp sibling then rename. A crash between
        write and rename leaves the previous good file intact."""
        # asdict walks nested dataclasses recursively — one call covers
        # current_agent, default_agent, each Agent in saved_agents, each
        # PollDefinition/PollVariable, each WatchDefinition, and all the
        # plain fields.
        data = asdict(settings)
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        payload = json.dumps(data, indent=2)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(payload)
        tmp.replace(self._path)


def default_settings_store() -> JsonFileSettingsStore:
    """The store used when callers don't pass one — settings.json under
    the user's XDG config directory."""
    return JsonFileSettingsStore(config_dir() / "settings.json")
