"""MibStore adapter — engine.ports.MibStore over MibTree + JSON cache.

Wraps two infra concerns: an in-memory ``MibTree`` (the resolved
catalogue) and a directory of compiled JSON files (the source of
truth). set_enabled() rebuilds the tree with the requested module
subset; the resolver returned by ``resolver()`` keeps working across
rebuilds because it forwards through the store rather than capturing
the tree at construction.
"""
from __future__ import annotations

from pathlib import Path

from ...engine.model import MibNodeView
from ..mib_loader import MibNode, MibTree


class _DynamicResolver:
    """Resolver view that always reads through the live tree on the
    store. Survives set_enabled() rebuilds — the captured store
    reference stays valid; the tree behind it changes."""

    def __init__(self, store: "MibTreeStore") -> None:
        self._store = store

    def resolve(self, name_or_oid: str) -> tuple[int, ...] | None:
        return self._store._tree.resolve_name(name_or_oid)


def _node_to_view(n: MibNode) -> MibNodeView:
    return MibNodeView(
        name=n.name,
        oid=n.oid,
        module=n.module,
        syntax=n.syntax,
        access=n.access,
        description=n.description,
        units=n.units,
        indices=tuple(n.indices),
        enum_values=tuple(sorted(n.enum_values.items())),
    )


class MibTreeStore:
    """MibStore implementation backed by a compiled-JSON directory and
    a MibTree. Stateful: holds the current tree and the directory path,
    nothing else."""

    def __init__(self, compiled_dir: Path, tree: MibTree | None = None,
                 enabled: list[str] | None = None) -> None:
        self._compiled_dir = Path(compiled_dir)
        self._tree = tree if tree is not None else MibTree()
        if tree is None:
            # Caller didn't supply a pre-built tree — load from cache now.
            self._tree.load_compiled(self._compiled_dir, enabled=enabled)

    # --- engine.ports.MibStore -----------------------------------------

    def resolver(self) -> _DynamicResolver:
        return _DynamicResolver(self)

    def available_modules(self) -> list[str]:
        if not self._compiled_dir.exists():
            return []
        return sorted(p.stem for p in self._compiled_dir.glob("*.json"))

    def enabled_modules(self) -> list[str]:
        return sorted(self._tree.modules.keys())

    def set_enabled(self, modules: list[str]) -> None:
        # Filter against what's actually on disk so callers can't
        # smuggle ghost modules into the tree.
        wanted = [m for m in modules if m in self.available_modules()]
        # Build a fresh tree — MibTree has no "unload" path. Cheap; the
        # JSON parse for ~50 modules is sub-100 ms on a laptop.
        self._tree = MibTree()
        self._tree.load_compiled(self._compiled_dir, enabled=wanted)

    def find_node(self, oid: tuple[int, ...]) -> MibNodeView | None:
        n = self._tree.lookup_oid(oid)
        return None if n is None else _node_to_view(n)

    # --- infra-specific accessors --------------------------------------

    @property
    def tree(self) -> MibTree:
        """Concrete tree — for callers that need full MibTree API
        (UI tree models, format_oid, etc.). Not part of the MibStore
        port; CLI/web consumers should not depend on this."""
        return self._tree
