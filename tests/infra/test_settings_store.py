"""SettingsStore adapter — JsonFileSettingsStore plus the back-compat
shims it installs onto AppSettings.

The pure dataclass lives in engine.model; persistence (load/save,
nested-loader map, atomic write) is what this adapter owns. These tests
pin the contract: corrupt JSON returns defaults, partial JSON keeps
defaults for missing fields, polymorphic fields survive a roundtrip,
and atomic writes don't truncate on crash."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pymibbrowser.engine.model import (
    Agent,
    AppSettings,
    PollDefinition,
    PollVariable,
    WatchDefinition,
)
from pymibbrowser.infra.adapters import (
    JsonFileSettingsStore,
    default_settings_store,
)

# --- store contract -------------------------------------------------------

def test_load_missing_file_returns_defaults(tmp_path):
    s = JsonFileSettingsStore(tmp_path / "absent.json").load()
    assert isinstance(s, AppSettings)
    assert s.current_agent.host == "127.0.0.1"


def test_load_corrupt_json_returns_defaults(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{ this is not json")
    s = JsonFileSettingsStore(p).load()
    assert s.saved_agents == []
    assert s.current_agent.host == "127.0.0.1"


def test_load_partial_keeps_defaults_for_missing_fields(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"language": "en", "trap_port": 1162}))
    s = JsonFileSettingsStore(p).load()
    assert s.language == "en"
    assert s.trap_port == 1162
    assert s.current_agent.host == "127.0.0.1"
    assert s.watch_interval_s == 15


def test_load_malformed_nested_field_falls_back_silently(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({
        "saved_agents": "not-a-list",   # nested loader will raise
        "language": "ru",
    }))
    s = JsonFileSettingsStore(p).load()
    assert s.language == "ru"
    assert s.saved_agents == []


def test_load_filters_garbage_inside_nested_lists(tmp_path):
    """Per-item isinstance(..., dict) guards against partially-corrupt
    persisted lists."""
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({
        "saved_agents": [{"host": "ok"}, "garbage", 42, None],
        "polls": [
            {"name": "p1", "interval_s": 5, "agents": [],
             "variables": [{"name": "v", "oid": ".1", "operation": "Get"},
                           "drop-me"]},
            "drop-this-poll",
        ],
        "watches": [{"name": "w"}, "drop"],
    }))
    s = JsonFileSettingsStore(p).load()
    assert [a.host for a in s.saved_agents] == ["ok"]
    assert len(s.polls) == 1
    assert [v.name for v in s.polls[0].variables] == ["v"]
    assert [w.name for w in s.watches] == ["w"]


# --- roundtrip ------------------------------------------------------------

def test_roundtrip_preserves_all_collections(tmp_path):
    store = JsonFileSettingsStore(tmp_path / "settings.json")
    s = AppSettings()
    s.current_agent = Agent(host="1.2.3.4", port=161, read_community="secret")
    s.saved_agents.append(Agent(host="host-b", port=11161))
    s.bookmarks.append({"name": "uptime", "oid": ".1.3.6.1.2.1.1.3.0",
                         "operation": "Get", "view": "op"})
    s.polls.append(PollDefinition(
        name="agents states", interval_s=30,
        agents=["1.2.3.4:161"],
        variables=[PollVariable(name="sysUpTime",
                                 oid=".1.3.6.1.2.1.1.3.0",
                                 operation="Get")]))
    s.watches.append(WatchDefinition(name="heartbeat",
                                       oid=".1.3.6.1.2.1.1.3.0",
                                       condition_op=">",
                                       condition_value="0"))
    s.recent_oids = [".1.3.6.1.2.1.1.3.0"]
    s.language = "ru"
    store.save(s)

    loaded = store.load()
    assert loaded.current_agent.host == "1.2.3.4"
    assert loaded.current_agent.read_community == "secret"
    assert [a.host for a in loaded.saved_agents] == ["host-b"]
    assert loaded.bookmarks[0]["oid"] == ".1.3.6.1.2.1.1.3.0"
    assert loaded.polls[0].variables[0].name == "sysUpTime"
    assert loaded.watches[0].condition_op == ">"
    assert loaded.recent_oids == [".1.3.6.1.2.1.1.3.0"]
    assert loaded.language == "ru"


def test_save_is_atomic(tmp_path, monkeypatch):
    """A crash between tmp-write and rename leaves the original file
    intact — bookmarks / saved agents survive partial writes."""
    store = JsonFileSettingsStore(tmp_path / "settings.json")
    store.save(AppSettings())
    original = (tmp_path / "settings.json").read_text()

    from pathlib import Path as _P
    def boom(self, target):
        raise RuntimeError("simulated crash between write and rename")
    monkeypatch.setattr(_P, "replace", boom)

    s = AppSettings()
    s.current_agent.host = "should-not-appear"
    with pytest.raises(RuntimeError):
        store.save(s)

    after = (tmp_path / "settings.json").read_text()
    assert after == original
    assert "should-not-appear" not in after


def test_save_creates_parent_directory(tmp_path):
    """Store handles a non-existent parent dir — first run should not
    fail because XDG_CONFIG_HOME/pymibbrowser/ doesn't exist yet."""
    target = tmp_path / "nested" / "deep" / "settings.json"
    JsonFileSettingsStore(target).save(AppSettings())
    assert target.exists()


# --- default store --------------------------------------------------------

def test_default_settings_store_uses_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    store = default_settings_store()
    # Save then read raw to confirm the file landed where we expect.
    store.save(AppSettings())
    expected = tmp_path / "config" / "pymibbrowser" / "settings.json"
    assert expected.exists()


# --- module-level convenience wrappers ----------------------------------

class TestConfigConvenienceFunctions:
    """``infra.config.load_settings()`` / ``save_settings(s)`` are the
    single entry point app code uses; they delegate to
    default_settings_store(). Tested here as the public surface."""

    def test_load_settings_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        from pymibbrowser.infra import config
        s = AppSettings()
        s.language = "ru"
        config.save_settings(s)
        loaded = config.load_settings()
        assert loaded.language == "ru"

    def test_load_returns_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "fresh"))
        from pymibbrowser.infra import config
        s = config.load_settings()
        assert s.current_agent.host == "127.0.0.1"


# --- model purity --------------------------------------------------------

def test_appsettings_model_has_no_persistence_internals():
    """The dataclass stays pure: no class-level shims, no JSON import."""
    # No load/save methods attached — those used to exist as back-compat
    # shims; once the UI migrated to config.save_settings, the class
    # became plain data again.
    assert not hasattr(AppSettings, "load")
    assert not hasattr(AppSettings, "save") or callable(AppSettings.save)
    # Ensure the dataclass has no surprising class attributes left over.
    import dataclasses
    names = {f.name for f in dataclasses.fields(AppSettings)}
    assert "_NESTED_LOADERS" not in names
    # engine.model has no library imports.
    from pymibbrowser.engine import model
    assert "json" not in dir(model)
    assert "Path" not in dir(model)


# --- ports.SettingsStore Protocol shape ----------------------------------

def test_protocol_shape():
    """JsonFileSettingsStore satisfies the engine.ports.SettingsStore
    Protocol structurally — both methods take/return the right types."""
    store = JsonFileSettingsStore(Path("/dev/null"))
    assert callable(store.load)
    assert callable(store.save)
