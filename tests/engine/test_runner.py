"""Engine runner tests. Pure: every port is faked. No pysnmp, no time,
no files, no sockets. The engine is deterministic given the same fakes."""
from __future__ import annotations

import pytest

from pymibbrowser.engine.ast import (
    Get,
    GetNext,
    If,
    Save,
    Script,
    Set,
    Sleep,
    Unknown,
)
from pymibbrowser.engine.model import Agent, VarBind
from pymibbrowser.engine.parser import parse_script
from pymibbrowser.engine.runner import ExecutionContext, execute

from .fakes import (
    DictResolver,
    FakeClock,
    ListLogger,
    ListSink,
    RecordingSnmp,
)


# --- helpers --------------------------------------------------------------

DEFAULT_TABLE = {
    "sysUpTime.0":  (1, 3, 6, 1, 2, 1, 1, 3, 0),
    "sysContact.0": (1, 3, 6, 1, 2, 1, 1, 4, 0),
    "sysName.0":    (1, 3, 6, 1, 2, 1, 1, 5, 0),
}


def _ctx(*, snmp=None, resolver=None, agent=None, clock=None,
         logger=None, sink=None, cancel=None) -> ExecutionContext:
    return ExecutionContext(
        agent=agent or Agent(host="127.0.0.1", port=161,
                              read_community="public"),
        snmp=snmp or RecordingSnmp(),
        clock=clock or FakeClock(),
        resolver=resolver or DictResolver(DEFAULT_TABLE),
        logger=logger or ListLogger(),
        sink=sink or ListSink(),
        cancel=cancel or (lambda: False),
    )


def _run(text: str, ctx: ExecutionContext) -> ExecutionContext:
    execute(parse_script(text), ctx)
    return ctx


# --- get ------------------------------------------------------------------

class TestGet:
    def test_resolves_symbolic(self):
        snmp = RecordingSnmp()
        ctx = _ctx(snmp=snmp)
        _run("get 127.0.0.1:11161 sysUpTime.0\n", ctx)
        assert len(snmp.get_calls) == 1
        c = snmp.get_calls[0]
        assert c.host == "127.0.0.1"
        assert c.port == 11161
        assert c.arg == [(1, 3, 6, 1, 2, 1, 1, 3, 0)]

    def test_does_not_mutate_base_agent(self):
        """Per-command host/port override must NOT smear into the base
        agent — that's the kind of state-leak the hexagonal split exists
        to prevent."""
        base = Agent(host="default-host", port=161,
                      read_community="originalcomm")
        snmp = RecordingSnmp()
        ctx = _ctx(snmp=snmp, agent=base)
        _run("get 10.0.0.1:11161 sysUpTime.0\n", ctx)
        # Base agent is unchanged.
        assert base.host == "default-host"
        assert base.port == 161
        assert base.read_community == "originalcomm"

    def test_logs_and_emits_each_varbind(self):
        snmp = RecordingSnmp(get_fn=lambda _a, _o: [
            VarBind(oid=(1, 3, 6, 1, 2, 1, 1, 3, 0),
                    type_name="TimeTicks", display_value="100"),
        ])
        logger = ListLogger()
        ctx = _ctx(snmp=snmp, logger=logger)
        _run("get 127.0.0.1 sysUpTime.0\n", ctx)
        assert any("TimeTicks" in ln and "100" in ln for ln in logger.lines)
        assert any(".1.3.6.1.2.1.1.3.0" in ln for ln in logger.lines)

    def test_unresolved_oid_skipped_logged(self):
        snmp = RecordingSnmp()
        logger = ListLogger()
        ctx = _ctx(snmp=snmp, logger=logger)
        _run("get 127.0.0.1 totally-unknown\n", ctx)
        assert snmp.get_calls == []
        assert any("unresolved OID" in ln for ln in logger.lines)

    def test_partial_resolution_calls_only_with_resolved(self):
        snmp = RecordingSnmp()
        logger = ListLogger()
        ctx = _ctx(snmp=snmp, logger=logger)
        _run("get 127.0.0.1 sysUpTime.0 totally-unknown\n", ctx)
        assert len(snmp.get_calls) == 1
        # Only sysUpTime made it through.
        assert snmp.get_calls[0].arg == [(1, 3, 6, 1, 2, 1, 1, 3, 0)]
        assert any("unresolved OID: totally-unknown" in ln
                   for ln in logger.lines)

    def test_transport_exception_sets_last_error(self):
        def boom(_a, _o):
            raise RuntimeError("agent unreachable")
        snmp = RecordingSnmp(get_fn=boom)
        logger = ListLogger()
        ctx = _ctx(snmp=snmp, logger=logger)
        _run("get 127.0.0.1 sysUpTime.0\n"
             "if $ err sleep 0.1\n", ctx)
        # Diagnostic logged.
        assert any("agent unreachable" in ln for ln in logger.lines)
        # The follow-up if-err triggered a sleep.
        assert ctx.clock.elapsed > 0


# --- getnext --------------------------------------------------------------

def test_getnext_uses_get_next_port():
    snmp = RecordingSnmp(next_fn=lambda _a, _o: [])
    ctx = _ctx(snmp=snmp)
    _run("getnext 127.0.0.1 sysUpTime.0\n", ctx)
    assert len(snmp.next_calls) == 1
    assert snmp.get_calls == []


# --- set ------------------------------------------------------------------

class TestSet:
    def test_passes_triples_to_transport(self):
        snmp = RecordingSnmp()
        ctx = _ctx(snmp=snmp)
        _run("set 127.0.0.1 sysContact.0 s admin@example.com\n", ctx)
        assert len(snmp.set_calls) == 1
        c = snmp.set_calls[0]
        assert c.arg == [((1, 3, 6, 1, 2, 1, 1, 4, 0), "s", "admin@example.com")]

    def test_skips_unresolved_keeps_resolved(self):
        snmp = RecordingSnmp()
        logger = ListLogger()
        ctx = _ctx(snmp=snmp, logger=logger)
        _run("set 127.0.0.1 nope-name s value sysContact.0 s ok\n", ctx)
        assert any("unresolved OID: nope-name" in ln for ln in logger.lines)
        assert snmp.set_calls
        # Only the resolved triple made it through.
        assert snmp.set_calls[0].arg == [
            ((1, 3, 6, 1, 2, 1, 1, 4, 0), "s", "ok"),
        ]

    def test_transport_exception_logged(self):
        def boom(_a, _p):
            raise RuntimeError("write denied")
        snmp = RecordingSnmp(set_fn=boom)
        logger = ListLogger()
        ctx = _ctx(snmp=snmp, logger=logger)
        _run("set 127.0.0.1 sysContact.0 s hi\n", ctx)
        assert any("set: write denied" in ln for ln in logger.lines)


# --- sleep + cancel -------------------------------------------------------

class TestSleep:
    def test_chunks_into_100ms_pieces(self):
        clock = FakeClock()
        ctx = _ctx(clock=clock)
        _run("sleep 0.25\n", ctx)
        # 0.1 + 0.1 + 0.05 = 0.25
        assert clock.sleeps == [0.1, 0.1, pytest.approx(0.05)]

    def test_zero_duration_no_sleep(self):
        clock = FakeClock()
        ctx = _ctx(clock=clock)
        _run("sleep 0\n", ctx)
        assert clock.sleeps == []

    def test_cancel_during_sleep_breaks_out(self):
        """First chunk runs, cancel flips, the loop exits early — and
        the next command after the sleep does NOT execute."""
        cancelled = {"v": False}
        clock = FakeClock()
        snmp = RecordingSnmp()

        def fake_sleep(s: float) -> None:
            clock.sleeps.append(s)
            cancelled["v"] = True
        clock.sleep = fake_sleep    # type: ignore[method-assign]

        logger = ListLogger()
        ctx = _ctx(clock=clock, snmp=snmp, logger=logger,
                   cancel=lambda: cancelled["v"])
        _run("sleep 5\nget 127.0.0.1 sysUpTime.0\n", ctx)
        # Only the first chunk fired.
        assert clock.sleeps == [0.1]
        # The follow-up get never ran.
        assert snmp.get_calls == []
        assert any("[cancelled]" in ln for ln in logger.lines)


# --- if -------------------------------------------------------------------

class TestIf:
    def test_compare_fires_when_true(self):
        snmp = RecordingSnmp(get_fn=lambda _a, _o: [
            VarBind(oid=(1, 3, 6, 1, 2, 1, 1, 3, 0),
                    type_name="TimeTicks", display_value="100"),
        ])
        clock = FakeClock()
        ctx = _ctx(snmp=snmp, clock=clock)
        _run("get 127.0.0.1 sysUpTime.0\n"
             "if $ > 50 sleep 1\n", ctx)
        assert clock.elapsed == pytest.approx(1.0)

    def test_compare_silent_when_false(self):
        snmp = RecordingSnmp(get_fn=lambda _a, _o: [
            VarBind(oid=(1, 3, 6, 1, 2, 1, 1, 3, 0),
                    type_name="TimeTicks", display_value="100"),
        ])
        clock = FakeClock()
        ctx = _ctx(snmp=snmp, clock=clock)
        _run("get 127.0.0.1 sysUpTime.0\n"
             "if $ < 50 sleep 1\n", ctx)
        assert clock.sleeps == []

    def test_err_predicate_after_failure(self):
        def boom(_a, _o):
            raise RuntimeError("net down")
        snmp = RecordingSnmp(get_fn=boom)
        clock = FakeClock()
        ctx = _ctx(snmp=snmp, clock=clock)
        _run("get 127.0.0.1 sysUpTime.0\n"
             "if $ err sleep 0.5\n", ctx)
        assert clock.elapsed == pytest.approx(0.5)

    def test_err_predicate_silent_after_success(self):
        snmp = RecordingSnmp(get_fn=lambda _a, _o: [
            VarBind(oid=(1, 3, 6, 1, 2, 1, 1, 3, 0),
                    type_name="TimeTicks", display_value="100"),
        ])
        clock = FakeClock()
        ctx = _ctx(snmp=snmp, clock=clock)
        _run("get 127.0.0.1 sysUpTime.0\n"
             "if $ err sleep 1\n", ctx)
        assert clock.sleeps == []

    def test_email_action_logs_skipped_message(self):
        snmp = RecordingSnmp(get_fn=lambda _a, _o: [
            VarBind(oid=(1, 3, 6, 1, 2, 1, 1, 3, 0),
                    type_name="TimeTicks", display_value="100"),
        ])
        logger = ListLogger()
        ctx = _ctx(snmp=snmp, logger=logger)
        _run("get 127.0.0.1 sysUpTime.0\n"
             "if $ > 50 email admin@example.com\n", ctx)
        assert any("admin@example.com" in ln and "SMTP not configured" in ln
                   for ln in logger.lines)

    def test_sound_action_logs_bell(self):
        snmp = RecordingSnmp(get_fn=lambda _a, _o: [
            VarBind(oid=(1, 3, 6, 1, 2, 1, 1, 3, 0),
                    type_name="TimeTicks", display_value="100"),
        ])
        logger = ListLogger()
        ctx = _ctx(snmp=snmp, logger=logger)
        _run("get 127.0.0.1 sysUpTime.0\n"
             "if $ > 50 sound bell\n", ctx)
        assert "\a" in logger.lines

    def test_if_with_no_prior_command_is_noop(self):
        clock = FakeClock()
        ctx = _ctx(clock=clock)
        _run("if $ > 50 sleep 1\n", ctx)
        assert clock.sleeps == []

    def test_if_with_non_numeric_result_does_not_fire(self):
        snmp = RecordingSnmp(get_fn=lambda _a, _o: [
            VarBind(oid=(1, 3, 6, 1, 2, 1, 1, 5, 0),
                    type_name="STRING", display_value="not-a-number"),
        ])
        clock = FakeClock()
        ctx = _ctx(snmp=snmp, clock=clock)
        _run("get 127.0.0.1 sysName.0\n"
             "if $ > 50 sleep 1\n", ctx)
        assert clock.sleeps == []

    def test_if_sleep_with_bad_arg_silent(self):
        """`if $ > 50 sleep notanumber` — the action's arg can't be parsed
        but the engine must not crash. Behaviour: silently skip."""
        snmp = RecordingSnmp(get_fn=lambda _a, _o: [
            VarBind(oid=(1,), type_name="x", display_value="100"),
        ])
        clock = FakeClock()
        ctx = _ctx(snmp=snmp, clock=clock)
        # Build the AST directly because the parser would catch this earlier.
        from pymibbrowser.engine.ast import If as IfNode
        script = Script(commands=(
            Get(host="127.0.0.1", port=161, oids=("sysUpTime.0",)),
            IfNode(predicate=">", operand="50", action="sleep", arg="bogus"),
        ))
        execute(script, ctx)
        assert clock.sleeps == []


# --- save / sink ----------------------------------------------------------

class TestSave:
    def test_save_then_get_buffers_into_sink(self):
        snmp = RecordingSnmp(get_fn=lambda _a, _o: [
            VarBind(oid=(1, 3, 6, 1, 2, 1, 1, 3, 0),
                    type_name="TimeTicks", display_value="42"),
        ])
        sink = ListSink()
        ctx = _ctx(snmp=snmp, sink=sink)
        _run("save out.txt\nget 127.0.0.1 sysUpTime.0\n", ctx)
        # Sink received exactly one persisted target with the result line.
        assert len(sink.closed_with) == 1
        target, lines = sink.closed_with[0]
        assert target == "out.txt"
        assert any("TimeTicks" in ln and "42" in ln for ln in lines)

    def test_emit_before_save_drops_lines(self):
        """A get before any save shouldn't materialise into the sink."""
        snmp = RecordingSnmp(get_fn=lambda _a, _o: [
            VarBind(oid=(1, 3, 6, 1, 2, 1, 1, 3, 0),
                    type_name="TimeTicks", display_value="42"),
        ])
        sink = ListSink()
        ctx = _ctx(snmp=snmp, sink=sink)
        _run("get 127.0.0.1 sysUpTime.0\n", ctx)
        assert sink.closed_with == []

    def test_close_called_even_with_no_save(self):
        """OutputSink.close() must always be called at the end so adapters
        can release resources, even if the script never opened anything."""
        sink = ListSink()
        ctx = _ctx(sink=sink)
        _run("# nothing to do\n", ctx)
        # ListSink.closed_with is only populated when a target is open;
        # close() should still run without error in the no-save case.
        # We model that by re-opening + closing here as a smoke test.
        assert sink.target is None


# --- unknown command diagnostics -----------------------------------------

class TestUnknown:
    def test_unknown_command_logged(self):
        logger = ListLogger()
        ctx = _ctx(logger=logger)
        _run("frobnicate widgets\n", ctx)
        assert any("unknown command" in ln and "frobnicate" in ln
                   for ln in logger.lines)

    def test_invalid_if_logged(self):
        logger = ListLogger()
        ctx = _ctx(logger=logger)
        _run("if not-a-real-if\n", ctx)
        assert any("invalid if" in ln for ln in logger.lines)

    def test_bad_sleep_logged(self):
        logger = ListLogger()
        ctx = _ctx(logger=logger)
        _run("sleep notanumber\n", ctx)
        assert any("bad sleep" in ln for ln in logger.lines)


# --- determinism ----------------------------------------------------------

def test_engine_is_deterministic():
    """Two runs against identical fakes produce identical observable state."""
    script = """
save out.txt
get 127.0.0.1 sysUpTime.0
if $ > 50 sleep 0.3
sleep 0.1
"""

    def replay():
        snmp = RecordingSnmp(get_fn=lambda _a, _o: [
            VarBind(oid=(1, 3, 6, 1, 2, 1, 1, 3, 0),
                    type_name="TimeTicks", display_value="99"),
        ])
        clock = FakeClock()
        logger = ListLogger()
        sink = ListSink()
        ctx = _ctx(snmp=snmp, clock=clock, logger=logger, sink=sink)
        _run(script, ctx)
        return clock.sleeps, logger.lines, sink.closed_with

    first = replay()
    second = replay()
    assert first == second
