"""Cover the path-helper and load-edge branches of pymibbrowser.infra.config
that the roundtrip suite doesn't reach."""
from __future__ import annotations

import json

import pytest

from pymibbrowser.infra import config
from pymibbrowser.infra.config import AppSettings


@pytest.fixture
def tmp_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    yield tmp_path


def test_config_dir_uses_xdg(tmp_xdg):
    p = config.config_dir()
    assert p == tmp_xdg / "config" / "pymibbrowser"
    assert p.is_dir()


def test_config_dir_falls_back_to_home(tmp_path, monkeypatch):
    """Without XDG_CONFIG_HOME, falls back to ~/.config/pymibbrowser."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = config.config_dir()
    assert p == tmp_path / ".config" / "pymibbrowser"
    assert p.is_dir()


def test_data_dir_uses_xdg(tmp_xdg):
    p = config.data_dir()
    assert p == tmp_xdg / "data" / "pymibbrowser"
    assert p.is_dir()


def test_data_dir_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = config.data_dir()
    assert p == tmp_path / ".local" / "share" / "pymibbrowser"
    assert p.is_dir()


def test_compiled_mibs_dir_under_data(tmp_xdg):
    p = config.compiled_mibs_dir()
    assert p == tmp_xdg / "data" / "pymibbrowser" / "compiled_mibs"
    assert p.is_dir()


def test_log_dir_default(tmp_xdg):
    """No custom path → logs/ under data_dir()."""
    p = config.log_dir()
    assert p == tmp_xdg / "data" / "pymibbrowser" / "logs"
    assert p.is_dir()


def test_log_dir_with_explicit_custom(tmp_path, tmp_xdg):
    """Caller passes the custom path explicitly — no module global."""
    custom = tmp_path / "alt-logs"
    p = config.log_dir(str(custom))
    assert p == custom
    assert custom.is_dir()


def test_log_dir_empty_custom_falls_back_to_default(tmp_xdg):
    """Empty / None custom is treated as 'use the default'."""
    assert config.log_dir(None) == tmp_xdg / "data" / "pymibbrowser" / "logs"
    assert config.log_dir("") == tmp_xdg / "data" / "pymibbrowser" / "logs"


def test_log_file_default(tmp_xdg):
    expected = tmp_xdg / "data" / "pymibbrowser" / "logs" / "pymibbrowser.log"
    assert config.log_file() == expected


def test_log_file_with_custom_dir(tmp_path, tmp_xdg):
    custom = tmp_path / "alt-logs"
    assert config.log_file(str(custom)) == custom / "pymibbrowser.log"


def test_no_module_globals_for_log_dir():
    """Hexagonal discipline: no _log_dir_override-style hidden state."""
    assert not hasattr(config, "_log_dir_override")
    assert not hasattr(config, "set_log_dir_override")


def test_project_root_resolves_above_package():
    root = config.project_root()
    # infra/config.py lives at <root>/pymibbrowser/infra/config.py
    assert (root / "pymibbrowser" / "infra" / "config.py").is_file()


def test_default_mibs_src_points_at_repo_dir():
    p = config.default_mibs_src()
    assert p.name == "mibs-src"
    assert p == config.project_root() / "mibs-src"


def test_load_corrupt_json_returns_default(tmp_xdg):
    """Truncated/garbage settings.json must not crash the app — load()
    silently falls back to defaults so the user can still launch."""
    cfg = config.config_dir() / "settings.json"
    cfg.write_text("{ this is not json")
    s = AppSettings.load()
    assert s.current_agent.host == "127.0.0.1"
    assert s.saved_agents == []


def test_load_partial_json_keeps_defaults_for_missing_fields(tmp_xdg):
    """A settings.json with only a subset of fields must keep the
    dataclass defaults for everything else (covers the
    `if f.name not in data: continue` branch)."""
    cfg = config.config_dir() / "settings.json"
    cfg.write_text(json.dumps({"language": "en", "trap_port": 1162}))
    s = AppSettings.load()
    assert s.language == "en"
    assert s.trap_port == 1162
    # Untouched fields keep their defaults.
    assert s.current_agent.host == "127.0.0.1"
    assert s.watch_interval_s == 15


def test_load_malformed_nested_field_falls_back(tmp_xdg):
    """If a nested-loader-handled field is the wrong shape, the loader
    swallows the exception and the field keeps its default — covers the
    `except Exception: continue` branch in load()."""
    cfg = config.config_dir() / "settings.json"
    cfg.write_text(json.dumps({
        # saved_agents must be a list of dicts; a string blows up the
        # nested loader. AppSettings.load should catch and skip it.
        "saved_agents": "not-a-list",
        "language": "ru",
    }))
    s = AppSettings.load()
    assert s.language == "ru"          # plain field still applied
    assert s.saved_agents == []         # default kept after loader error


def test_load_skips_non_dict_entries_in_lists(tmp_xdg):
    """Per-item `isinstance(..., dict)` filters in _NESTED_LOADERS guard
    against partially-corrupt persisted lists."""
    cfg = config.config_dir() / "settings.json"
    cfg.write_text(json.dumps({
        "saved_agents": [{"host": "ok"}, "garbage", 42, None],
        "polls": [
            {"name": "p1", "interval_s": 5, "agents": [],
             "variables": [{"name": "v", "oid": ".1", "operation": "Get"},
                           "drop-me"]},
            "drop-this-poll",
        ],
        "watches": [{"name": "w"}, "drop"],
    }))
    s = AppSettings.load()
    assert [a.host for a in s.saved_agents] == ["ok"]
    assert len(s.polls) == 1
    assert [v.name for v in s.polls[0].variables] == ["v"]
    assert [w.name for w in s.watches] == ["w"]
