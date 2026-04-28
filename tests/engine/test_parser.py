"""Parser is pure: text in, AST out. No I/O of any kind."""
from __future__ import annotations

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
from pymibbrowser.engine.parser import parse_command, parse_script


# --- empty / comments ----------------------------------------------------

def test_empty_script():
    assert parse_script("").commands == ()


def test_comments_and_blank_lines_dropped():
    s = parse_script("\n# first comment\n\n# second\n")
    assert s.commands == ()


# --- get / getnext --------------------------------------------------------

def test_get_simple():
    cmd = parse_command("get 127.0.0.1 sysUpTime.0")
    assert cmd == Get(host="127.0.0.1", port=161, oids=("sysUpTime.0",))


def test_get_with_explicit_port():
    cmd = parse_command("get 10.0.0.1:11161 sysUpTime.0 sysContact.0")
    assert isinstance(cmd, Get)
    assert cmd.host == "10.0.0.1"
    assert cmd.port == 11161
    assert cmd.oids == ("sysUpTime.0", "sysContact.0")


def test_get_default_port_overridable():
    cmd = parse_command("get 10.0.0.1 sysUpTime.0", default_port=10162)
    assert cmd.port == 10162


def test_get_with_invalid_port_falls_back():
    """Caller passed 'host:not-a-number' — we keep the host raw and use
    the default port (matches old behaviour: don't error, just dispatch
    and let the runner's error path handle a downstream resolution miss
    or socket failure)."""
    cmd = parse_command("get host:notaport sysUpTime.0", default_port=161)
    assert cmd.host == "host:notaport"
    assert cmd.port == 161


def test_getnext():
    cmd = parse_command("getnext 127.0.0.1 sysUpTime")
    assert cmd == GetNext(host="127.0.0.1", port=161, oids=("sysUpTime",))


def test_get_too_few_args_unknown():
    cmd = parse_command("get 127.0.0.1")
    assert isinstance(cmd, Unknown)


# --- set ------------------------------------------------------------------

def test_set_single_triple():
    cmd = parse_command("set 127.0.0.1 sysContact.0 s admin@example.com")
    assert cmd == Set(host="127.0.0.1", port=161,
                       triples=(("sysContact.0", "s", "admin@example.com"),))


def test_set_multiple_triples():
    cmd = parse_command("set 1.2.3.4 oid1 i 10 oid2 s hi")
    assert isinstance(cmd, Set)
    assert cmd.triples == (("oid1", "i", "10"), ("oid2", "s", "hi"))


def test_set_unbalanced_triples_unknown():
    cmd = parse_command("set 1.2.3.4 oid1 i")    # missing value
    assert isinstance(cmd, Unknown)


# --- sleep ----------------------------------------------------------------

def test_sleep_float():
    assert parse_command("sleep 0.25") == Sleep(seconds=0.25)


def test_sleep_integer():
    assert parse_command("sleep 5") == Sleep(seconds=5.0)


def test_sleep_missing_arg_unknown():
    cmd = parse_command("sleep")
    assert cmd == Unknown(text="sleep", reason="bad sleep")


def test_sleep_bad_arg_unknown():
    cmd = parse_command("sleep notanumber")
    assert isinstance(cmd, Unknown)
    assert cmd.reason == "bad sleep"


# --- save -----------------------------------------------------------------

def test_save_path():
    assert parse_command("save /tmp/out.txt") == Save(target="/tmp/out.txt")


def test_save_path_with_spaces():
    assert parse_command("save /tmp/my file.txt") == Save(target="/tmp/my file.txt")


def test_save_no_arg_unknown():
    assert isinstance(parse_command("save"), Unknown)


# --- if -------------------------------------------------------------------

def test_if_err_with_action():
    cmd = parse_command("if $ err sleep 5")
    assert cmd == If(predicate="err", operand="", action="sleep", arg="5")


def test_if_err_action_no_arg():
    cmd = parse_command("if $ err sound")
    assert cmd == If(predicate="err", operand="", action="sound", arg="")


def test_if_compare_gt():
    cmd = parse_command("if $ > 50 sleep 1")
    assert cmd == If(predicate=">", operand="50", action="sleep", arg="1")


def test_if_compare_all_operators():
    for op in (">=", "<=", "!=", ">", "<", "="):
        cmd = parse_command(f"if $ {op} 10 sleep 1")
        assert isinstance(cmd, If)
        assert cmd.predicate == op
        assert cmd.operand == "10"


def test_if_compare_email_with_recipient():
    cmd = parse_command("if $ > 90 email admin@example.com")
    assert cmd == If(predicate=">", operand="90",
                     action="email", arg="admin@example.com")


def test_if_unparseable_unknown():
    cmd = parse_command("if total nonsense")
    assert cmd == Unknown(text="if total nonsense", reason="invalid if")


# --- unknown --------------------------------------------------------------

def test_unknown_command():
    cmd = parse_command("frobnicate widgets")
    assert cmd == Unknown(text="frobnicate widgets", reason="unknown command")


# --- whole-script integration --------------------------------------------

def test_full_script():
    text = """
# header
save /tmp/out.txt

get 127.0.0.1 sysUpTime.0
if $ err sleep 1
sleep 0.5
"""
    s = parse_script(text)
    assert s.commands == (
        Save(target="/tmp/out.txt"),
        Get(host="127.0.0.1", port=161, oids=("sysUpTime.0",)),
        If(predicate="err", operand="", action="sleep", arg="1"),
        Sleep(seconds=0.5),
    )


def test_script_is_immutable_dataclass():
    """Scripts are frozen — they're values, not mutable buffers, so
    the same AST can be replayed safely."""
    s = parse_script("get 1.2.3.4 sysName.0")
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.commands = ()       # type: ignore[misc]
