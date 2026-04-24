"""Result-table model for SNMP query results."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ..i18n import _t
from ..mib_loader import MibTree
from ..snmp_ops import VarBind

COLUMNS = ("Name/OID", "Value", "Type", "IP:Port")


class ResultTableModel(QAbstractTableModel):
    def __init__(self, tree: MibTree | None = None) -> None:
        super().__init__()
        self._tree = tree
        self._rows: list[tuple[VarBind, str]] = []   # (vb, source_host)

    def set_tree(self, t: MibTree) -> None:
        self._tree = t

    # Data manipulation -------------------------------------------------

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    def add(self, vb: VarBind, source: str) -> None:
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append((vb, source))
        self.endInsertRows()

    def extend(self, vbs: list[VarBind], source: str) -> None:
        if not vbs:
            return
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(vbs) - 1)
        for vb in vbs:
            self._rows.append((vb, source))
        self.endInsertRows()

    def row_count(self) -> int:
        return len(self._rows)

    def get(self, row: int) -> tuple[VarBind, str]:
        return self._rows[row]

    # QAbstractTableModel ----------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        vb, src = self._rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.EditRole:
            if col == 0:
                if self._tree is not None:
                    return self._tree.format_oid(vb.oid)
                return "." + ".".join(str(p) for p in vb.oid)
            if col == 1:
                # Enum resolution: translate integer to enum name if MIB says so
                if self._tree is not None and vb.type_name in ("Integer", "Integer32"):
                    node = self._tree.lookup_oid(vb.oid[:-1]) or self._tree.lookup_oid(vb.oid)
                    if node and node.enum_values:
                        try:
                            iv = int(vb.display_value)
                            if iv in node.enum_values:
                                return f"{node.enum_values[iv]}({iv})"
                        except (ValueError, TypeError):
                            pass
                return vb.display_value
            if col == 2:
                return vb.type_name
            if col == 3:
                return src
        if role == Qt.ItemDataRole.ToolTipRole:
            # Expand the OID into a full tooltip: symbolic name,
            # module, dotted form, syntax/access. Same info the
            # Properties panel shows, but hoverable inline.
            lines: list[str] = []
            dotted = "." + ".".join(str(p) for p in vb.oid)
            if self._tree is not None:
                node = self._tree.lookup_oid(vb.oid)
                if node is None and len(vb.oid) > 1:
                    node = self._tree.lookup_oid(vb.oid[:-1])
                if node is not None:
                    lines.append(f"{node.name}")
                    if node.module:
                        lines.append(f"MIB: {node.module}")
                    if node.syntax:
                        lines.append(f"syntax: {node.syntax}")
                    if node.access:
                        lines.append(f"access: {node.access}")
            lines.append(dotted)
            return "\n".join(lines)
        return None

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return _t(COLUMNS[section])
            return str(section + 1)
        return None
