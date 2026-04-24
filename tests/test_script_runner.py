"""script_runner — exercise the parser without hitting the network.

We replace snmp_ops.op_get/op_next/op_set with stubs so each command's
parsing, OID resolution and conditional branching is tested in isolation.
"""
from __future__ import annotations

import pytest

from pymibbrowser import script_runner
from pymibbrowser.config import Agent
from pymibbrowser.mib_loader import MibNode, MibTree
from pymibbrowser.snmp_ops import VarBind


class _Tree:
    """Minimal tree that only needs to resolve a couple of names."""

    def __init__(self, names: dict[str, tuple]):
        self.names = names

    def resolve_name(self, text: str):
        text = text.strip()
        if text in self.names:
            return self.names[text]
        # Fall through: parse dotted numeric.
        try:
            return tuple(int(p) for p in text.strip(".").split("."))
        except ValueError:
            return None


@pytest.fixture
def stub_snmp(monkeypatch):
    calls = {"get": [], "next": [], "set": []}

    def op_get(agent, oids):
        calls["get"].append((agent.host, agent.port, list(oids)))
        # Return numeric counters — picked up by 'if $ < 60'.
        return [VarBind(oid=oids[0], type_name="TimeTicks",
                         value=None, display_value="100")]

    def op_next(agent, oids):
        calls["next"].append((agent.host, agent.port, list(oids)))
        return []

    def op_set(agent, pairs):
        calls["set"].append((agent.host, agent.port, list(pairs)))
        return []

    monkeypatch.setattr(script_runner.snmp_ops, "op_get", op_get)
    monkeypatch.setattr(script_runner.snmp_ops, "op_next", op_next)
    monkeypatch.setattr(script_runner.snmp_ops, "op_set", op_set)
    return calls


@pytest.fixture
def tree():
    return _Tree({"sysUpTime.0": (1, 3, 6, 1, 2, 1, 1, 3, 0),
                   "sysContact.0": (1, 3, 6, 1, 2, 1, 1, 4, 0)})


def _run(tmp_path, content, tree, log=None):
    script = tmp_path / "s.txt"
    script.write_text(content)
    collected = log if log is not None else []
    script_runner.run(str(script), Agent(host="127.0.0.1"), tree,
                      logger=collected.append)
    return collected


def test_get_resolves_symbolic(tmp_path, stub_snmp, tree):
    log = _run(tmp_path, "get 127.0.0.1:11161 sysUpTime.0\n", tree)
    assert stub_snmp["get"]
    host, port, oids = stub_snmp["get"][0]
    assert host == "127.0.0.1" and port == 11161
    assert oids == [(1, 3, 6, 1, 2, 1, 1, 3, 0)]
    # Result logged.
    assert any("sysUpTime" in ln or ".1.3.6.1.2.1.1.3.0" in ln
               for ln in log)


def test_comment_and_blank_lines_skipped(tmp_path, stub_snmp, tree):
    _run(tmp_path, "# hello\n\nget 127.0.0.1 sysUpTime.0\n",
         tree)
    assert len(stub_snmp["get"]) == 1


def test_unresolved_oid_logs_and_skips(tmp_path, stub_snmp, tree):
    log = _run(tmp_path, "get 127.0.0.1 nope-not-a-mib-name\n", tree)
    assert not stub_snmp["get"]
    assert any("unresolved OID" in ln for ln in log)


def test_set_parses_triples(tmp_path, stub_snmp, tree):
    # 'i' = integer type tag. Value '42'.
    _run(tmp_path,
         "set 127.0.0.1 sysContact.0 s admin@example.com\n", tree)
    assert stub_snmp["set"]
    _, _, pairs = stub_snmp["set"][0]
    assert pairs
    assert pairs[0][0] == (1, 3, 6, 1, 2, 1, 1, 4, 0)


def test_if_fires_action_when_matches(tmp_path, stub_snmp, tree,
                                       monkeypatch):
    # The first get returns "100"; 'if $ > 50 sleep 0' triggers.
    sleeps: list[float] = []
    monkeypatch.setattr(script_runner.time, "sleep",
                        lambda s: sleeps.append(s))
    _run(tmp_path,
         "get 127.0.0.1 sysUpTime.0\n"
         "if $ > 50 sleep 0\n", tree)
    assert sleeps == [0.0]


def test_if_does_not_fire_when_condition_false(tmp_path, stub_snmp,
                                                 tree, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(script_runner.time, "sleep",
                        lambda s: sleeps.append(s))
    _run(tmp_path,
         "get 127.0.0.1 sysUpTime.0\n"
         "if $ < 50 sleep 99\n", tree)
    assert sleeps == []


def test_sleep_blocks_monkeypatched(tmp_path, stub_snmp, tree,
                                     monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(script_runner.time, "sleep",
                        lambda s: sleeps.append(s))
    _run(tmp_path, "sleep 0.25\n", tree)
    assert sleeps == [0.25]
