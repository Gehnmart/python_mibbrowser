"""Adapter tests. Each adapter is a thin translator — these tests pin
the translation contract (input shape on the engine side ↔ output shape
on the infra side) and the few behavioural details that aren't
mechanical (FileSink's no-overwrite, NumericResolver's strictness)."""
from __future__ import annotations

import pytest

from pymibbrowser.engine.model import Agent
from pymibbrowser.engine.model import VarBind as EngineVarBind
from pymibbrowser.infra import snmp_ops
from pymibbrowser.infra.adapters import (
    CallbackLogger,
    FileSink,
    MibTreeResolver,
    NullSink,
    NumericResolver,
    PrintLogger,
    PysnmpTransport,
    WallClock,
)
from pymibbrowser.infra.mib_loader import MibTree
from pymibbrowser.infra.snmp_ops import VarBind as RawVarBind

# --- WallClock ------------------------------------------------------------

def test_wall_clock_sleeps_via_time(monkeypatch):
    sleeps: list[float] = []
    import time
    monkeypatch.setattr(time, "sleep", sleeps.append)
    WallClock().sleep(0.01)
    assert sleeps == [0.01]


def test_wall_clock_now_is_monotonic():
    c = WallClock()
    a = c.now(); b = c.now()
    assert b >= a


# --- Logger adapters ------------------------------------------------------

def test_callback_logger_forwards():
    captured: list[str] = []
    CallbackLogger(captured.append).log("hello")
    assert captured == ["hello"]


def test_print_logger_writes_to_stdout(capsys):
    PrintLogger().log("line one")
    out, _err = capsys.readouterr()
    assert out.strip() == "line one"


# --- FileSink -------------------------------------------------------------

class TestFileSink:
    def test_buffers_then_writes_on_close(self, tmp_path):
        out = tmp_path / "results.txt"
        s = FileSink()
        s.open(str(out))
        s.emit("line1"); s.emit("line2")
        assert not out.exists()      # not yet flushed
        s.close()
        assert out.read_text() == "line1\nline2"

    def test_emit_before_open_is_dropped(self, tmp_path):
        s = FileSink()
        s.emit("ignored")            # no target → no-op
        s.close()                    # nothing to do
        assert not any(tmp_path.iterdir())

    def test_close_without_emit_does_not_create_file(self, tmp_path):
        s = FileSink()
        s.open(str(tmp_path / "out.txt"))
        s.close()
        assert not (tmp_path / "out.txt").exists()

    def test_no_overwrite_appends_numeric_suffix(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("original")
        s = FileSink()
        s.open(str(target))
        s.emit("fresh")
        s.close()
        # Original untouched.
        assert target.read_text() == "original"
        # Fresh capture exists at out.txt.1.
        sibling = tmp_path / "out.txt.1"
        assert sibling.read_text() == "fresh"

    def test_collision_walks_through_indices(self, tmp_path):
        """Two collisions in a row → out.txt.2."""
        (tmp_path / "out.txt").write_text("a")
        (tmp_path / "out.txt.1").write_text("b")
        s = FileSink()
        s.open(str(tmp_path / "out.txt"))
        s.emit("c")
        s.close()
        assert (tmp_path / "out.txt.2").read_text() == "c"

    def test_close_creates_missing_parent_dir(self, tmp_path):
        """`save subdir/out.txt` with subdir/ absent must not crash on
        close — the parent is created on demand."""
        target = tmp_path / "fresh" / "deep" / "out.txt"
        s = FileSink()
        s.open(str(target))
        s.emit("hello")
        s.close()
        assert target.read_text() == "hello"

    def test_open_replaces_previous_target(self, tmp_path):
        """If the script issues `save A` then `save B` — A is dropped,
        only B's path receives the eventual write."""
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        s = FileSink()
        s.open(str(a))
        s.emit("never-flushed")
        s.open(str(b))               # supersedes a
        s.emit("kept")
        s.close()
        assert not a.exists()
        assert b.read_text() == "kept"

    def test_on_persist_callback_fires_with_path_and_count(self, tmp_path):
        out = tmp_path / "out.txt"
        seen: list[tuple] = []
        s = FileSink(on_persist=lambda p, n: seen.append((p, n)))
        s.open(str(out))
        s.emit("a"); s.emit("b"); s.emit("c")
        s.close()
        assert seen == [(out, 3)]

    def test_on_persist_silent_when_buffer_empty(self, tmp_path):
        seen: list[tuple] = []
        s = FileSink(on_persist=lambda p, n: seen.append((p, n)))
        s.open(str(tmp_path / "out.txt"))
        s.close()                    # no emit → no callback
        assert seen == []


def test_null_sink_swallows_everything(tmp_path):
    s = NullSink()
    s.open(str(tmp_path / "anything"))
    s.emit("x"); s.emit("y")
    s.close()
    assert list(tmp_path.iterdir()) == []


# --- Resolver adapters ----------------------------------------------------

class TestNumericResolver:
    def test_accepts_dotted_numeric(self):
        r = NumericResolver()
        assert r.resolve("1.3.6.1.2.1.1.3.0") == (1, 3, 6, 1, 2, 1, 1, 3, 0)

    def test_accepts_leading_dot(self):
        assert NumericResolver().resolve(".1.3.6.1") == (1, 3, 6, 1)

    def test_rejects_symbolic(self):
        assert NumericResolver().resolve("sysUpTime") is None

    def test_rejects_empty(self):
        assert NumericResolver().resolve("") is None
        assert NumericResolver().resolve(".") is None

    def test_rejects_mixed(self):
        assert NumericResolver().resolve("1.3.foo.4") is None


def test_mib_tree_resolver_delegates():
    """Adapter is a one-line wrapper; just verify it talks to the tree."""
    tree = MibTree()
    r = MibTreeResolver(tree)
    # iso (1,) is a bootstrap node — resolves both numerically and by name.
    assert r.resolve("1") == (1,)
    assert r.resolve("iso") == (1,)
    assert r.resolve("noSuchSymbol") is None


# --- PysnmpTransport ------------------------------------------------------

class TestPysnmpTransport:
    @pytest.fixture
    def stub_ops(self, monkeypatch):
        calls = {"get": [], "next": [], "set": []}

        def op_get(agent, oids):
            calls["get"].append((agent.host, list(oids)))
            return [RawVarBind(oid=oids[0], type_name="TimeTicks",
                                value="<pysnmp-internal>", display_value="42")]

        def op_next(_a, oids):
            return [RawVarBind(oid=(1, 3), type_name="OctetString",
                                value=b"raw", display_value="raw")]

        def op_set(agent, pairs):
            calls["set"].append((agent.host, list(pairs)))
            return [RawVarBind(oid=pairs[0][0], type_name="Integer32",
                                value=42, display_value="42")]

        monkeypatch.setattr(snmp_ops, "op_get", op_get)
        monkeypatch.setattr(snmp_ops, "op_next", op_next)
        monkeypatch.setattr(snmp_ops, "op_set", op_set)
        return calls

    def test_get_strips_raw_value(self, stub_ops):
        out = PysnmpTransport().get(Agent(host="h"),
                                      [(1, 3, 6, 1, 2, 1, 1, 3, 0)])
        # Engine.VarBind is a 3-field dataclass — no `.value` slot.
        assert isinstance(out, list)
        assert isinstance(out[0], EngineVarBind)
        assert not hasattr(out[0], "value")
        assert out[0].oid == (1, 3, 6, 1, 2, 1, 1, 3, 0)
        assert out[0].type_name == "TimeTicks"
        assert out[0].display_value == "42"

    def test_get_next_returns_engine_varbinds(self, stub_ops):
        out = PysnmpTransport().get_next(Agent(), [(1,)])
        assert all(isinstance(vb, EngineVarBind) for vb in out)

    def test_set_encodes_via_build_set_value(self, stub_ops):
        """The adapter calls build_set_value with each (tag, raw) before
        forwarding. Stub op_set records the encoded pairs to confirm."""
        PysnmpTransport().set(
            Agent(host="h"),
            [((1, 3, 6, 1, 2, 1, 1, 6, 0), "i", "42")])
        assert stub_ops["set"]
        _host, encoded = stub_ops["set"][0]
        from pysnmp.proto import rfc1902
        oid, value = encoded[0]
        assert oid == (1, 3, 6, 1, 2, 1, 1, 6, 0)
        assert isinstance(value, rfc1902.Integer32)
        assert int(value) == 42

    def test_no_per_instance_state(self):
        """Adapters must be safely shareable between executions —
        i.e. constructing one and inspecting __dict__ shows nothing."""
        t = PysnmpTransport()
        assert t.__dict__ == {}
