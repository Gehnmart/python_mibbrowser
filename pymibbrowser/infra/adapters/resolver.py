"""Resolver adapters — engine.ports.Resolver implementations."""
from __future__ import annotations

from ..mib_loader import MibTree


class MibTreeResolver:
    """Wraps a MibTree so the engine can resolve symbolic OIDs without
    knowing the tree exists."""

    def __init__(self, tree: MibTree) -> None:
        self._tree = tree

    def resolve(self, name_or_oid: str) -> tuple[int, ...] | None:
        return self._tree.resolve_name(name_or_oid)


class NumericResolver:
    """Strict numeric-only resolver. Useful when no MIBs are loaded —
    accepts dotted decimals (with or without a leading dot) and rejects
    everything else."""

    def resolve(self, name_or_oid: str) -> tuple[int, ...] | None:
        s = name_or_oid.strip().lstrip(".")
        if not s:
            return None
        if not all(p.isdigit() for p in s.split(".")):
            return None
        return tuple(int(p) for p in s.split("."))
