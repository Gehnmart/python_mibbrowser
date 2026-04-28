"""MibCompiler adapter — engine.ports.MibCompiler over pysmi.

Owns the per-module compile loop. pysmi's high-level ``compiler.compile()``
can take many modules at once but doesn't emit per-module progress, so
we drive the loop ourselves and yield a ``CompileResult`` after each
module finishes. ``should_cancel`` is polled between modules — pysmi
has no cooperative cancel within a single module compile.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from ...engine.model import CompileResult
from ..mib_loader import _discover_modules, _make_compiler

log = logging.getLogger(__name__)


class PysmiMibCompiler:
    """Compile MIB sources into a JSON cache via pysmi.

    ``cache_dir`` is the output directory — every successful compile
    drops a ``<MODULE>.json`` there. Adapters share a cache across many
    invocations; the directory is created on first compile if missing."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)

    def discover(self, source_dirs: list[Path]) -> list[str]:
        # _discover_modules walks the filesystem in path order; the
        # MibCompiler port promises sorted+deduped output to callers.
        return sorted(set(_discover_modules([Path(d) for d in source_dirs])))

    def compile(self, modules: list[str], source_dirs: list[Path], *,
                rebuild: bool = False,
                use_network: bool = False,
                on_progress: Callable[[CompileResult, int, int], None] | None = None,
                should_cancel: Callable[[], bool] | None = None,
                ) -> list[CompileResult]:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        compiler = _make_compiler(
            [Path(d) for d in source_dirs],
            self._cache_dir,
            use_network=use_network)

        results: list[CompileResult] = []
        total = len(modules)
        log.info("Compiling %d MIB modules into %s (network=%s)",
                  total, self._cache_dir, use_network)
        for i, mod in enumerate(modules, 1):
            if should_cancel is not None and should_cancel():
                log.info("compile cancelled at %d/%d", i, total)
                break
            log.info("[%d/%d] compiling %s …", i, total, mod)
            t0 = time.monotonic()
            try:
                raw = compiler.compile(mod, rebuild=rebuild,
                                         genTexts=True, ignoreErrors=True)
            except Exception as exc:
                raw = {mod: f"failed: {exc}"}
                log.exception("[%d/%d] exception while compiling %s",
                              i, total, mod)
            dt = time.monotonic() - t0
            status = str(raw.get(mod, ""))
            level = logging.WARNING if dt > 3 else logging.INFO
            log.log(level, "[%d/%d] %s → %s in %.2fs",
                     i, total, mod, status, dt)
            res = CompileResult(module=mod, status=status)
            results.append(res)
            if on_progress is not None:
                try:
                    on_progress(res, i, total)
                except Exception:
                    log.exception("compile progress callback error")
        return results
