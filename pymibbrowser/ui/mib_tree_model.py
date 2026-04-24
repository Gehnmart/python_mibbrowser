"""QAbstractItemModel for the MIB tree.

We don't expose the raw MibNode graph directly. Chains of single-child
nodes (iso → org → dod → internet → mgmt → mib-2) are collapsed into a
single virtual node whose display string is the dotted concatenation
("iso.org.dod.internet.mgmt.mib-2"). This matches how iReasoning shows
the tree and keeps the user from clicking through six trivial folders.

A VirtNode's `real` field points to the tail of the chain — that's the
node whose attributes (OID, access, syntax, …) represent the visible
item, and it's what the rest of the app gets back via `node_for_index`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, QSortFilterProxyModel, Qt

from ..mib_loader import MibNode, MibTree


@dataclass
class VirtNode:
    display: str                        # "iso.org.dod…mib-2"
    real: MibNode                       # tail of the collapsed chain
    parent: VirtNode | None = None
    children: list[VirtNode] = field(default_factory=list)


# Roles that mark a node as a *real item* (table, row, scalar, column,
# notification, …). Such nodes stay as their own tree entry even when
# they have exactly one child — we only merge purely-organisational
# folder nodes into their parent's dotted label.
_ITEM_ROLES = {"table", "row", "scalar", "column", "notification"}


def _is_folder(n: MibNode) -> bool:
    """An organisational container — no SMI syntax/access attached and
    not flagged as any of the SMI item kinds."""
    if n.syntax or n.access:
        return False
    if (n.node_role or "").lower() in _ITEM_ROLES:
        return False
    return True


def _collapse(real: MibNode, parent_v: VirtNode | None,
              oid_map: dict[tuple[int, ...], VirtNode]) -> VirtNode:
    """Walk down a single-child chain of *folders* from `real` and build
    one VirtNode whose label is the dotted concatenation. Record every
    real OID in the chain so find_index() can resolve mid-chain OIDs."""
    names = [real.name]
    chain_oids = [real.oid]
    cur = real
    # Only merge while both ends of the step are folders — otherwise the
    # child (table/row/scalar/…) deserves its own row in the tree.
    while (_is_folder(cur) and len(cur.children) == 1
           and _is_folder(cur.children[0])):
        cur = cur.children[0]
        names.append(cur.name)
        chain_oids.append(cur.oid)
    v = VirtNode(display=".".join(names), real=cur, parent=parent_v)
    for oid in chain_oids:
        oid_map[oid] = v
    v.children = [_collapse(c, v, oid_map) for c in cur.children]
    return v


def build_virt_tree(
    root: MibNode, single_root: bool = True,
) -> tuple[VirtNode, dict[tuple[int, ...], VirtNode]]:
    """Build the virtual tree.

    When single_root is True (default), the phantom (invisible) root sits
    above a single top-level VirtNode whose chain starts at the real
    `root` — so the user sees the whole iso…mib-2 chain as one visible
    item and everything else nested inside it.

    When single_root is False, the collapsed top chain is bypassed and
    the children of the first branching node become the top-level visible
    rows — useful when you always work below mib-2 and want to skip the
    one-click descent through iso."""
    oid_map: dict[tuple[int, ...], VirtNode] = {}
    phantom = VirtNode(display="", real=root, parent=None)
    top = _collapse(root, phantom, oid_map)
    if single_root:
        phantom.children = [top]
    else:
        # Re-parent the chain's children directly onto phantom. This keeps
        # oid_map intact (entries for mid-chain OIDs still point at `top`,
        # which remains the logical anchor for find_index — but find_index
        # walks parent chain, so we need to rewire parents too).
        for c in top.children:
            c.parent = phantom
        phantom.children = top.children
        # Mid-chain OIDs that previously resolved to `top` no longer have
        # a visible VirtNode to land on. Drop them — find_index will
        # return an invalid index for e.g. .1.3.6.1 which is fine in
        # single_root=False mode.
        dead_oids = [k for k, v in oid_map.items() if v is top]
        for k in dead_oids:
            del oid_map[k]
    return phantom, oid_map


class FastMibFilterProxy(QSortFilterProxyModel):
    """
    Custom recursive filter proxy for MIB tree. Matches against the
    virtual node's display string (so a chain collapses as a unit)
    and pre-computes per-node match status to avoid the quadratic
    blow-up QSortFilterProxyModel does by default.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._needle = ""
        self._cache: dict[int, bool] = {}  # id(VirtNode) → matched
        self._search_descriptions = False
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def setSearchDescriptions(self, enabled: bool) -> None:
        """When enabled, the filter also matches against each node's
        DESCRIPTION text — useful for finding 'CPU'/'temperature'/etc.
        when the node names don't spell it out. Triggers a recompute."""
        if enabled == self._search_descriptions:
            return
        self._search_descriptions = enabled
        self._cache.clear()
        src_model = self.sourceModel()
        if src_model is not None and self._needle:
            self._precompute(src_model._root_v)  # type: ignore[attr-defined]
        self.invalidateFilter()

    def setFilterFixedString(self, s: str) -> None:
        self._needle = (s or "").lower()
        self._cache.clear()
        src_model = self.sourceModel()
        if src_model is not None and self._needle:
            root_v = src_model._root_v  # type: ignore[attr-defined]
            self._precompute(root_v)
        super().setFilterFixedString(s)

    def _precompute(self, v: VirtNode) -> bool:
        matched = self._needle in v.display.lower()
        if (not matched and self._search_descriptions
                and v.real.description):
            matched = self._needle in v.real.description.lower()
        for c in v.children:
            if self._precompute(c):
                matched = True
        self._cache[id(v)] = matched
        return matched

    def _matches_any(self, v: VirtNode) -> bool:
        if not self._needle:
            return True
        return self._cache.get(id(v), True)

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        if not self._needle:
            return True
        src = self.sourceModel()
        idx = src.index(row, 0, parent)
        v = idx.internalPointer() if idx.isValid() else None
        if v is None:
            return True
        return self._matches_any(v)


ICON_NAMES = {
    "table": "📋",
    "row": "📑",
    "rw": "📝",
    "ro": "📖",
    "notification": "🔔",
    "group": "📁",
    "leaf": "•",
}


class MibTreeModel(QAbstractItemModel):
    def __init__(self, tree: MibTree, single_root: bool = True) -> None:
        super().__init__()
        self._tree = tree
        self._single_root = single_root
        self._root_v, self._oid_to_virt = build_virt_tree(
            tree.root, single_root=single_root)

    # --- helpers -------------------------------------------------------

    def _virt(self, index: QModelIndex) -> VirtNode:
        if index.isValid():
            return index.internalPointer()  # type: ignore[return-value]
        return self._root_v

    def node_for_index(self, index: QModelIndex) -> MibNode | None:
        """Return the *real* MibNode behind a QModelIndex — i.e. the tail
        of whatever collapsed chain is shown there. The rest of the app
        doesn't need to know about virtual nodes."""
        if not index.isValid():
            return None
        v: VirtNode = index.internalPointer()
        return v.real

    def find_index(self, oid: tuple[int, ...]) -> QModelIndex:
        v = self._oid_to_virt.get(tuple(oid))
        if v is None:
            return QModelIndex()
        # Walk ancestors back to the root to build the index chain.
        chain: list[VirtNode] = []
        cur: VirtNode | None = v
        while cur is not None and cur is not self._root_v:
            chain.append(cur)
            cur = cur.parent
        chain.reverse()
        idx = QModelIndex()
        parent = self._root_v
        for n in chain:
            try:
                row = parent.children.index(n)
            except ValueError:
                return QModelIndex()
            idx = self.index(row, 0, idx)
            parent = n
        return idx

    # --- QAbstractItemModel --------------------------------------------

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._virt(parent).children)

    def index(self, row: int, column: int,
              parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if row < 0 or column != 0:
            return QModelIndex()
        p = self._virt(parent)
        if row >= len(p.children):
            return QModelIndex()
        return self.createIndex(row, column, p.children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        v: VirtNode = index.internalPointer()
        p = v.parent
        if p is None or p is self._root_v:
            return QModelIndex()
        gp = p.parent or self._root_v
        try:
            row = gp.children.index(p)
        except ValueError:
            return QModelIndex()
        return self.createIndex(row, 0, p)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        v: VirtNode = index.internalPointer()
        n = v.real
        if role == Qt.ItemDataRole.DisplayRole:
            icon = self._icon_for(v)
            return f"{icon}  {v.display}"
        if role == Qt.ItemDataRole.ToolTipRole:
            parts = [f"<b>{v.display}</b>"]
            if n.module:
                parts.append(f"<i>{n.module}</i>")
            parts.append("." + ".".join(str(p) for p in n.oid))
            if n.syntax:
                parts.append(f"syntax: {n.syntax}")
            if n.access:
                parts.append(f"access: {n.access}")
            if n.description:
                desc = n.description.replace("\n", " ")
                if len(desc) > 180:
                    desc = desc[:180] + "..."
                parts.append(f"<br>{desc}")
            return "<br>".join(parts)
        return None

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and section == 0:
            return "MIB Tree"
        return None

    def _icon_for(self, v: VirtNode) -> str:
        # Icon reflects the tail of the chain — that's the leaf-ish node
        # whose role actually matters (access mode, is-table, etc.).
        n = v.real
        role = n.node_role
        if role == "table":
            return ICON_NAMES["table"]
        if role == "row":
            return ICON_NAMES["row"]
        if role == "notification" or n.node_type == "notificationtype":
            return ICON_NAMES["notification"]
        access = (n.access or "").lower()
        if "write" in access or "create" in access:
            return ICON_NAMES["rw"]
        if "read" in access:
            return ICON_NAMES["ro"]
        if v.children:
            return ICON_NAMES["group"]
        return ICON_NAMES["leaf"]
