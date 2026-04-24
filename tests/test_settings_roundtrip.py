"""AppSettings save → load roundtrip — catches regressions where a new
field lands in save() but not in load() (or vice versa)."""
from __future__ import annotations

import pytest

from pymibbrowser.config import (
    Agent,
    AppSettings,
    PollDefinition,
    PollVariable,
    WatchDefinition,
)


@pytest.fixture
def tmp_xdg(tmp_path, monkeypatch):
    """Re-point XDG_CONFIG_HOME at a scratch dir so load/save touch a
    throw-away settings.json rather than the user's real one."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    yield tmp_path


def test_empty_roundtrip(tmp_xdg):
    s = AppSettings()
    s.save()
    loaded = AppSettings.load()
    assert loaded.current_agent.host == s.current_agent.host
    assert loaded.saved_agents == []
    assert loaded.polls == []
    assert loaded.watches == []


def test_roundtrip_preserves_all_collections(tmp_xdg):
    s = AppSettings()
    s.current_agent = Agent(host="1.2.3.4", port=161,
                             read_community="secret")
    s.saved_agents.append(Agent(host="host-b", port=11161))
    s.default_agent = Agent(host="0.0.0.0", timeout_s=5.0)
    s.bookmarks.append({"name": "uptime",
                         "oid": ".1.3.6.1.2.1.1.3.0",
                         "operation": "Get", "view": "op"})
    s.polls.append(PollDefinition(
        name="agents states", interval_s=30,
        agents=["1.2.3.4:161"],
        variables=[PollVariable(name="sysUpTime",
                                 oid=".1.3.6.1.2.1.1.3.0",
                                 operation="Get")]))
    s.watches.append(WatchDefinition(
        name="heartbeat", oid=".1.3.6.1.2.1.1.3.0",
        condition_op=">", condition_value="0"))
    s.recent_oids = [".1.3.6.1.2.1.1.3.0", ".1.3.6.1.2.1.2.2.1.10.1"]
    s.language = "ru"
    s.watch_interval_s = 23
    s.save()

    loaded = AppSettings.load()
    assert loaded.current_agent.host == "1.2.3.4"
    assert loaded.current_agent.read_community == "secret"
    assert len(loaded.saved_agents) == 1
    assert loaded.saved_agents[0].host == "host-b"
    assert loaded.default_agent.timeout_s == 5.0
    assert loaded.bookmarks[0]["oid"] == ".1.3.6.1.2.1.1.3.0"
    assert len(loaded.polls) == 1 and loaded.polls[0].name == "agents states"
    assert loaded.polls[0].variables[0].name == "sysUpTime"
    assert len(loaded.watches) == 1 and loaded.watches[0].condition_op == ">"
    assert loaded.recent_oids == [".1.3.6.1.2.1.1.3.0",
                                   ".1.3.6.1.2.1.2.2.1.10.1"]
    assert loaded.language == "ru"
    assert loaded.watch_interval_s == 23


def test_save_is_atomic(tmp_xdg, monkeypatch):
    """Partial writes shouldn't leave the real settings.json empty. We
    simulate a crash between the tmp-write and the rename — settings.json
    should still be the previous good version (or absent)."""
    s = AppSettings()
    s.save()
    original = (tmp_xdg / "config" / "pymibbrowser" /
                "settings.json").read_text()

    # Monkeypatch Path.replace to raise, so the rename step fails after
    # the .tmp file was written.
    from pathlib import Path as _P

    def boom(self, target):
        raise RuntimeError("simulated crash between write and rename")

    monkeypatch.setattr(_P, "replace", boom)

    s.current_agent.host = "should-not-appear"
    with pytest.raises(RuntimeError):
        s.save()

    # settings.json on disk is still the pre-crash content.
    after = (tmp_xdg / "config" / "pymibbrowser" /
             "settings.json").read_text()
    assert after == original
    assert "should-not-appear" not in after


def test_load_missing_file_is_default(tmp_xdg):
    # A fresh install (no settings.json yet) must not crash.
    s = AppSettings.load()
    assert s.current_agent.host == "127.0.0.1"
    assert s.polls == []
