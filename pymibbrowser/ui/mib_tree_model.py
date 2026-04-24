"""QAbstractItemModel for the MIB tree. Lazy children via dataclass graph."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, Qt, QSortFilterProxyModel
from PyQt6.QtGui import QIcon

from ..mib_loader import MibNode, MibTree


class FastMibFilterProxy(QSortFilterProxyModel):
    """
    Custom recursive filter proxy for MIB tree. Directly accesses MibNode
    objects rather than going through data() (which formats emoji-prefixed
    strings per call); caches per-OID match results so a subtree with a
    matching descendant isn't re-scanned on every ancestor's filterAcceptsRow.

    Reduces time from 14 s to ~50 ms for substring filters across 5 000 nodes.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._needle = ""
        self._cache: dict[tuple[int, ...], bool] = {}
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def setFilterFixedString(self, s: str) -> None:
        self._needle = (s or "").lower()
        self._cache.clear()
        # Eagerly pre-compute match status for EVERY node in the tree before
        # Qt starts calling filterAcceptsRow. Otherwise the first call on each
        # top-level subtree walks its descendants while siblings remain
        # un-cached, causing quadratic behaviour (observed ~15 s for 5 000
        # nodes on a 2-char substring).
        src_model = self.sourceModel()
        if src_model is not None and self._needle:
            root = src_model._tree.root  # type: ignore[attr-defined]
            self._precompute(root)
        super().setFilterFixedString(s)

    def _precompute(self, node: MibNode) -> bool:
        """Post-order fill of the cache: a node matches iff its own name
        matches OR any descendant does."""
        matched = self._needle in node.name.lower()
        for c in node.children:
            if self._precompute(c):
                matched = True
        self._cache[node.oid] = matched
        return matched

    def _matches_any(self, node: MibNode) -> bool:
        if not self._needle:
            return True
        # Cache populated by _precompute; fall back to None-safe default.
        return self._cache.get(node.oid, True)

    _call_count = 0

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        FastMibFilterProxy._call_count += 1
        if not self._needle:
            return True
        src = self.sourceModel()
        idx = src.index(row, 0, parent)
        n = idx.internalPointer() if idx.isValid() else None
        if n is None:
            return True
        return self._matches_any(n)


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
    def __init__(self, tree: MibTree) -> None:
        super().__init__()
        self._tree = tree

    # --- helpers -------------------------------------------------------

    def _node(self, index: QModelIndex) -> MibNode:
        if index.isValid():
            return index.internalPointer()  # type: ignore[return-value]
        return self._tree.root

    def node_for_index(self, index: QModelIndex) -> Optional[MibNode]:
        if not index.isValid():
            return None
        return index.internalPointer()  # type: ignore[return-value]

    def find_index(self, oid: tuple[int, ...]) -> QModelIndex:
        """Produce a QModelIndex chain leading to the node with this OID, or an
        invalid index if the OID isn't in the tree."""
        node = self._tree.node_by_oid(tuple(oid))
        if node is None:
            return QModelIndex()
        # Walk ancestors back to root to build the chain.
        chain: list[MibNode] = []
        cur: Optional[MibNode] = node
        while cur is not None and cur is not self._tree.root:
            chain.append(cur)
            cur = cur.parent
        chain.reverse()
        idx = QModelIndex()
        parent = self._tree.root
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
        n = self._node(parent)
        return len(n.children)

    def index(self, row: int, column: int,
              parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if row < 0 or column != 0:
            return QModelIndex()
        p = self._node(parent)
        if row >= len(p.children):
            return QModelIndex()
        return self.createIndex(row, column, p.children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        n: MibNode = index.internalPointer()
        p = n.parent
        if p is None or p is self._tree.root:
            return QModelIndex()
        gp = p.parent or self._tree.root
        try:
            row = gp.children.index(p)
        except ValueError:
            return QModelIndex()
        return self.createIndex(row, 0, p)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        n: MibNode = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            icon = self._icon_for(n)
            return f"{icon}  {n.name}"
        if role == Qt.ItemDataRole.ToolTipRole:
            parts = [f"<b>{n.name}</b>"]
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

    def _icon_for(self, n: MibNode) -> str:
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
        if n.children:
            return ICON_NAMES["group"]
        return ICON_NAMES["leaf"]
