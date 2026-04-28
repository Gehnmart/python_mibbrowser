"""PysmiMibCompiler adapter tests.

The pysmi internals are real (we use the bundled mibs-src/ fixture),
but the adapter behaviours under test — discover() output, per-module
progress callback, should_cancel honoured between modules,
CompileResult.ok flag — don't depend on which specific MIB compiles
first."""
from __future__ import annotations

import pytest

from pymibbrowser.engine.model import CompileResult
from pymibbrowser.infra import config
from pymibbrowser.infra.adapters import PysmiMibCompiler


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


# --- discover -------------------------------------------------------------

def test_discover_lists_modules_in_source_dir(cache):
    compiler = PysmiMibCompiler(cache)
    src = config.default_mibs_src()
    if not src.exists():
        pytest.skip("repo mibs-src/ not present")
    out = compiler.discover([src])
    assert out, "no modules discovered"
    assert all(isinstance(m, str) for m in out)
    # Sorted, no duplicates.
    assert out == sorted(set(out))


def test_discover_handles_missing_source_dir(cache, tmp_path):
    """A non-existent source dir is silently skipped (matches _discover_modules)."""
    compiler = PysmiMibCompiler(cache)
    assert compiler.discover([tmp_path / "absent"]) == []


def test_discover_walks_extension_variants(cache, tmp_path):
    """File suffixes accepted by pysmi: .mib / .my / .txt / .smi / no ext."""
    src = tmp_path / "src"; src.mkdir()
    (src / "FOO.mib").write_text("")
    (src / "BAR.txt").write_text("")
    (src / "BAZ").write_text("")
    out = PysmiMibCompiler(cache).discover([src])
    assert set(out) == {"FOO", "BAR", "BAZ"}


def test_discover_skips_non_mib_extensions(cache, tmp_path):
    """README.md, .gitignore, *.pdf etc. shipped alongside MIBs in the
    source tree must not enter the compile queue — pysmi would only
    fail loudly on them, polluting the per-module status report."""
    src = tmp_path / "src"; src.mkdir()
    (src / "REAL-MIB.mib").write_text("")
    (src / "BARE").write_text("")          # no extension — keep
    (src / "README.md").write_text("docs")
    (src / "docs.pdf").write_text("pdf")
    (src / ".gitignore").write_text("ignore")
    out = PysmiMibCompiler(cache).discover([src])
    assert set(out) == {"REAL-MIB", "BARE"}


def test_discover_skips_stub_mibs(cache, tmp_path):
    """Framework MIBs that pysnmp provides at runtime (ASN1, SNMPv2-SMI,
    ...) are filtered — compiling them would be redundant."""
    src = tmp_path / "src"; src.mkdir()
    (src / "ASN1.mib").write_text("")
    (src / "USEFUL.mib").write_text("")
    out = PysmiMibCompiler(cache).discover([src])
    assert "ASN1" not in out
    assert "USEFUL" in out


def test_discover_dedups_across_source_dirs(cache, tmp_path):
    """Same module shipped under multiple source roots collapses to one."""
    d1 = tmp_path / "d1"; d1.mkdir()
    d2 = tmp_path / "d2"; d2.mkdir()
    (d1 / "FOO.mib").write_text("")
    (d2 / "FOO.mib").write_text("")
    out = PysmiMibCompiler(cache).discover([d1, d2])
    assert out == ["FOO"]


# --- compile --------------------------------------------------------------

class TestCompile:
    @pytest.fixture
    def repo_src(self):
        src = config.default_mibs_src()
        if not src.exists():
            pytest.skip("repo mibs-src/ not present")
        return src

    def test_compile_one_module(self, cache, repo_src):
        compiler = PysmiMibCompiler(cache)
        # Pick the first discovered module.
        modules = compiler.discover([repo_src])[:1]
        results = compiler.compile(modules, [repo_src])
        assert len(results) == 1
        assert results[0].module == modules[0]
        assert isinstance(results[0], CompileResult)
        # Cache file landed somewhere — at minimum the JSON cache dir
        # has at least one file (could be a dependency, not the module
        # itself, depending on pysmi resolution order).
        assert any(cache.glob("*.json"))

    def test_progress_callback_per_module(self, cache, repo_src):
        compiler = PysmiMibCompiler(cache)
        modules = compiler.discover([repo_src])[:3]
        progress: list[tuple[CompileResult, int, int]] = []
        compiler.compile(modules, [repo_src],
                          on_progress=lambda r, i, n: progress.append((r, i, n)))
        # One progress event per module.
        assert len(progress) == len(modules)
        # done counter increments from 1 to len; total stays constant.
        assert [p[1] for p in progress] == list(range(1, len(modules) + 1))
        assert {p[2] for p in progress} == {len(modules)}

    def test_should_cancel_stops_between_modules(self, cache, repo_src):
        compiler = PysmiMibCompiler(cache)
        modules = compiler.discover([repo_src])[:5]
        cancel_after = {"v": False}
        seen: list[CompileResult] = []

        def on_progress(_r, i, _n):
            seen.append(_r)
            if i == 2:
                cancel_after["v"] = True

        results = compiler.compile(
            modules, [repo_src],
            on_progress=on_progress,
            should_cancel=lambda: cancel_after["v"])
        # The third module never started — only the first two ran.
        assert len(results) == 2
        assert len(seen) == 2

    def test_progress_callback_exception_swallowed(self, cache, repo_src):
        """A misbehaving callback can't crash the compile loop."""
        compiler = PysmiMibCompiler(cache)
        modules = compiler.discover([repo_src])[:1]

        def boom(_r, _i, _n):
            raise RuntimeError("callback error")

        results = compiler.compile(modules, [repo_src], on_progress=boom)
        assert len(results) == 1


def test_compile_creates_cache_dir(tmp_path):
    """cache_dir doesn't have to exist at construction — first compile
    creates it. Matters for first-run flow where ~/.local/share/pymibbrowser
    is freshly being populated."""
    cache = tmp_path / "fresh" / "deep" / "cache"
    src = config.default_mibs_src()
    if not src.exists():
        pytest.skip("repo mibs-src/ not present")
    compiler = PysmiMibCompiler(cache)
    modules = compiler.discover([src])[:1]
    compiler.compile(modules, [src])
    assert cache.is_dir()


# --- CompileResult --------------------------------------------------------

class TestCompileResult:
    def test_ok_when_status_is_clean(self):
        assert CompileResult(module="X", status="compiled").ok is True
        assert CompileResult(module="X", status="untouched").ok is True
        assert CompileResult(module="X", status="").ok is True

    def test_not_ok_when_status_starts_with_failed(self):
        assert CompileResult(module="X", status="failed: parse error").ok is False

    def test_immutable(self):
        r = CompileResult(module="X", status="compiled")
        import dataclasses
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.module = "Y"   # type: ignore[misc]
