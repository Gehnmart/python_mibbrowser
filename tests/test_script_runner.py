"""Integration tests for infra.script_runner — the file-level shim that
reads a script off disk, builds production adapters, and hands the AST to
engine.execute. The per-command behaviour is exercised exhaustively in
tests/engine/test_runner.py with pure fakes; here we only verify the
adapter wiring works end-to-end against the real infra modules."""
from __future__ import annotations

import pytest

from pymibbrowser.engine.model import Agent
from pymibbrowser.infra import script_runner, snmp_ops
from pymibbrowser.infra.mib_loader import MibTree
from pymibbrowser.infra.snmp_ops import VarBind as RawVarBind


@pytest.fixture
def tree():
    """Real MibTree with bootstrap nodes — enough to resolve dotted
    numeric OIDs and the iso/mib-2 etc. canonical names."""
    return MibTree()


@pytest.fixture
def stub_pysnmp(monkeypatch):
    """Stub the lowest-level pysnmp ops — adapters call into these."""
    calls = {"get": [], "next": [], "set": []}

    def op_get(agent, oids):
        calls["get"].append((agent.host, agent.port, list(oids)))
        return [RawVarBind(oid=oids[0], type_name="TimeTicks",
                            value=None, display_value="123")]

    def op_next(agent, oids):
        calls["next"].append((agent.host, agent.port, list(oids)))
        return []

    def op_set(agent, pairs):
        calls["set"].append((agent.host, agent.port, list(pairs)))
        return []

    monkeypatch.setattr(snmp_ops, "op_get", op_get)
    monkeypatch.setattr(snmp_ops, "op_next", op_next)
    monkeypatch.setattr(snmp_ops, "op_set", op_set)
    return calls


def _write(tmp_path, body: str):
    p = tmp_path / "s.txt"
    p.write_text(body)
    return p


# --- file → engine wiring -------------------------------------------------

def test_reads_file_and_dispatches_get(tmp_path, tree, stub_pysnmp):
    log: list[str] = []
    p = _write(tmp_path, "get 127.0.0.1:11161 1.3.6.1.2.1.1.3.0\n")
    script_runner.run(str(p), Agent(host="default", port=161),
                      tree, logger=log.append)
    # Adapter forwarded to the real op_get with the parsed host/port.
    assert stub_pysnmp["get"] == [
        ("127.0.0.1", 11161, [(1, 3, 6, 1, 2, 1, 1, 3, 0)]),
    ]
    # Result line surfaced through the logger.
    assert any("TimeTicks" in ln and "123" in ln for ln in log)


def test_default_port_taken_from_agent(tmp_path, tree, stub_pysnmp):
    """When the script omits the port, the parser uses agent.port."""
    p = _write(tmp_path, "get 10.0.0.1 1.3.6.1.2.1.1.5.0\n")
    script_runner.run(str(p), Agent(host="default", port=11162), tree,
                      logger=lambda _l: None)
    assert stub_pysnmp["get"][0][1] == 11162


def test_set_round_trips_through_build_set_value(tmp_path, tree, stub_pysnmp):
    """Adapter encodes the type tag → rfc1902 type before calling op_set."""
    p = _write(tmp_path, "set 127.0.0.1 1.3.6.1.2.1.1.6.0 i 42\n")
    script_runner.run(str(p), Agent(), tree, logger=lambda _l: None)
    assert stub_pysnmp["set"]
    _host, _port, pairs = stub_pysnmp["set"][0]
    oid, encoded = pairs[0]
    assert oid == (1, 3, 6, 1, 2, 1, 1, 6, 0)
    # build_set_value('i', '42') → rfc1902.Integer32(42).
    from pysnmp.proto import rfc1902
    assert isinstance(encoded, rfc1902.Integer32)
    assert int(encoded) == 42


def test_save_writes_buffered_lines_to_file(tmp_path, tree, stub_pysnmp):
    out = tmp_path / "results.txt"
    log: list[str] = []
    p = _write(tmp_path,
               f"save {out}\nget 127.0.0.1 1.3.6.1.2.1.1.3.0\n")
    script_runner.run(str(p), Agent(), tree, logger=log.append)
    assert out.exists()
    body = out.read_text()
    assert "TimeTicks" in body and "123" in body
    assert any("saved" in ln and str(out) in ln for ln in log)


def test_save_does_not_overwrite_existing_file(tmp_path, tree, stub_pysnmp):
    out = tmp_path / "results.txt"
    out.write_text("preexisting")
    p = _write(tmp_path,
               f"save {out}\nget 127.0.0.1 1.3.6.1.2.1.1.3.0\n")
    script_runner.run(str(p), Agent(), tree, logger=lambda _l: None)
    assert out.read_text() == "preexisting"
    assert any(c.name.startswith("results.txt.") for c in tmp_path.iterdir())


def test_should_cancel_breaks_out(tmp_path, tree, stub_pysnmp, monkeypatch):
    """Cancel during a long sleep: subsequent commands don't execute."""
    cancelled = {"v": False}
    sleeps: list[float] = []

    import time
    def fake_sleep(s):
        sleeps.append(s)
        cancelled["v"] = True
    monkeypatch.setattr(time, "sleep", fake_sleep)

    log: list[str] = []
    p = _write(tmp_path,
               "sleep 5\nget 127.0.0.1 1.3.6.1.2.1.1.3.0\n")
    script_runner.run(str(p), Agent(), tree, logger=log.append,
                      should_cancel=lambda: cancelled["v"])
    # Only the first 100 ms chunk fired.
    assert sleeps == [0.1]
    # The follow-up get was not executed.
    assert stub_pysnmp["get"] == []
    assert any("[cancelled]" in ln for ln in log)


def test_default_logger_prints_when_no_callback(tmp_path, tree, stub_pysnmp,
                                                  capsys):
    """Without a logger argument, the shim falls back to PrintLogger."""
    p = _write(tmp_path, "get 127.0.0.1 1.3.6.1.2.1.1.3.0\n")
    script_runner.run(str(p), Agent(), tree)
    out, _err = capsys.readouterr()
    assert "TimeTicks" in out
