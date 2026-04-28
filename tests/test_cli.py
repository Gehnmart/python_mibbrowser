"""CLI tests: command parsing, dispatch, and the load-bearing
architectural guarantee that the CLI doesn't drag in Qt."""
from __future__ import annotations

import sys

import pytest

from pymibbrowser import cli
from pymibbrowser.engine.model import Agent
from pymibbrowser.infra import snmp_ops
from pymibbrowser.infra.snmp_ops import VarBind as RawVarBind


# --- the architectural assertion ------------------------------------------

def test_cli_does_not_import_qt():
    """The whole reason the engine + adapters split exists. If a future
    refactor accidentally drags PyQt into the CLI's import graph, this
    pin catches it before review.

    Run in a fresh subprocess — the test process itself may have PyQt
    loaded transitively from other tests, so an in-process check
    against sys.modules wouldn't be definitive."""
    import subprocess
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; import pymibbrowser.cli; "
         "qt = [m for m in sys.modules "
         "      if m.startswith(('PyQt6', 'PyQt5', 'PySide2', 'PySide6'))]; "
         "print('LEAKED' if qt else 'CLEAN', qt)"],
        capture_output=True, text=True, check=True)
    assert out.stdout.startswith("CLEAN"), \
        f"CLI dragged Qt into its import graph:\n{out.stdout}\n{out.stderr}"


# --- argument parser shape ------------------------------------------------

class TestParser:
    def test_run_with_minimal_args(self):
        args = cli.build_parser().parse_args(["run", "script.txt"])
        assert args.command == "run"
        assert args.script == "script.txt"
        assert args.host is None
        assert args.port is None

    def test_run_with_all_overrides(self):
        args = cli.build_parser().parse_args([
            "run", "s.txt", "--host", "10.0.0.1", "--port", "11161",
            "--community", "secret", "--version", "1",
            "--save", "/tmp/out.txt"])
        assert args.host == "10.0.0.1"
        assert args.port == 11161
        assert args.community == "secret"
        assert args.version == "1"
        assert args.save == "/tmp/out.txt"

    def test_modules_default_is_enabled(self):
        args = cli.build_parser().parse_args(["modules"])
        assert args.command == "modules"
        assert not args.list_available
        assert not args.enabled

    def test_modules_list_available(self):
        args = cli.build_parser().parse_args(["modules", "--list-available"])
        assert args.list_available is True

    def test_no_command_fails(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args([])

    def test_run_without_script_fails(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["run"])


# --- run command end-to-end ----------------------------------------------

@pytest.fixture
def stub_pysnmp(monkeypatch):
    """Replace pysnmp ops with stubs so no UDP traffic is generated."""
    calls = {"get": []}

    def op_get(agent, oids):
        calls["get"].append((agent.host, agent.port, list(oids)))
        return [RawVarBind(oid=oids[0], type_name="TimeTicks",
                            value=None, display_value="123")]

    monkeypatch.setattr(snmp_ops, "op_get", op_get)
    monkeypatch.setattr(snmp_ops, "op_next", lambda a, o: [])
    monkeypatch.setattr(snmp_ops, "op_set", lambda a, p: [])
    return calls


@pytest.fixture
def fresh_xdg(tmp_path, monkeypatch):
    """Isolate from the user's real settings/MIB cache."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


def test_run_dispatches_get_through_engine(tmp_path, fresh_xdg, stub_pysnmp,
                                              capsys):
    script = tmp_path / "s.txt"
    script.write_text("get 127.0.0.1:11161 1.3.6.1.2.1.1.3.0\n")
    rc = cli.main(["run", str(script), "--host", "ignored",
                    "--port", "11161", "--community", "public"])
    assert rc == 0
    # The stub recorded the call with the script's host:port (not --host).
    assert stub_pysnmp["get"]
    host, port, oids = stub_pysnmp["get"][0]
    assert host == "127.0.0.1"
    assert port == 11161
    assert oids == [(1, 3, 6, 1, 2, 1, 1, 3, 0)]
    out = capsys.readouterr().out
    assert "TimeTicks" in out and "123" in out


def test_run_missing_script_returns_2(fresh_xdg, capsys):
    rc = cli.main(["run", "/no/such/file.txt"])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_run_with_save_writes_file(tmp_path, fresh_xdg, stub_pysnmp, capsys):
    script = tmp_path / "s.txt"
    script.write_text("get 127.0.0.1 1.3.6.1.2.1.1.3.0\n")
    out = tmp_path / "out.txt"
    rc = cli.main(["run", str(script), "--save", str(out)])
    assert rc == 0
    assert out.exists()
    assert "TimeTicks" in out.read_text()
    # Logger announces the persist path.
    assert f"saved" in capsys.readouterr().out


# --- modules command -----------------------------------------------------

def test_modules_lists_enabled(fresh_xdg, capsys, monkeypatch):
    """Stub MibTreeStore so we don't depend on real compiled JSONs."""
    from pymibbrowser.infra import adapters
    class _StubStore:
        def __init__(self, *_a, **_kw): pass
        def available_modules(self): return ["VENDOR-MIB", "ZZZ-MIB"]
        def enabled_modules(self):   return ["VENDOR-MIB"]
        @property
        def tree(self):              return None
    monkeypatch.setattr(adapters, "MibTreeStore", _StubStore)
    # cli imports MibTreeStore at module import time, not at call time —
    # patch the attribute on the cli module too.
    monkeypatch.setattr(cli, "MibTreeStore", _StubStore)

    rc = cli.main(["modules"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "VENDOR-MIB" in out and "ZZZ-MIB" not in out


def test_modules_lists_available(fresh_xdg, capsys, monkeypatch):
    from pymibbrowser.infra import adapters
    class _StubStore:
        def __init__(self, *_a, **_kw): pass
        def available_modules(self): return ["A-MIB", "B-MIB"]
        def enabled_modules(self):   return ["A-MIB"]
        @property
        def tree(self):              return None
    monkeypatch.setattr(adapters, "MibTreeStore", _StubStore)
    monkeypatch.setattr(cli, "MibTreeStore", _StubStore)

    rc = cli.main(["modules", "--list-available"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "A-MIB" in out and "B-MIB" in out
