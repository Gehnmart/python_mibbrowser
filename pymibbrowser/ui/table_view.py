"""Table View tab — renders SNMP table data as a grid (rows = instances,
cols = columns of the MIB table)."""
from __future__ import annotations

import csv

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import workers
from ..infra import snmp_ops
from ..infra.config import Agent
from ..infra.i18n import _t
from ..infra.index_parser import parse_index_suffix
from ..infra.mib_loader import MibTree


class TableDataModel(QAbstractTableModel):
    """
    Columns = MIB columns + a synthetic trailing "Index Value" column showing
    the raw OID instance suffix (for easy copy-paste when building a manual
    GET on a specific row). iReasoning's Table View has the same thing.
    """

    INDEX_VALUE_LABEL = "Index Value"

    def __init__(self, column_names: list[str]) -> None:
        super().__init__()
        self._columns = [*list(column_names), self.INDEX_VALUE_LABEL]
        self._rows: list[list[str]] = []
        self._row_index: list[str] = []     # suffix of OID, e.g. "1.2"
        self._rotated = False

    def rowCount(self, parent=QModelIndex()) -> int:   return len(self._rows)
    def columnCount(self, parent=QModelIndex()) -> int: return len(self._columns)

    def data(self, idx: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not idx.isValid():
            return None
        col = idx.column()
        row = idx.row()
        is_idx_col = (not self._rotated
                      and col == len(self._columns) - 1
                      and self._columns[col] == self.INDEX_VALUE_LABEL)
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            data_row = self._rows[row]
            if col >= len(data_row):
                return None
            val = data_row[col]
            if is_idx_col:
                return f"[{row + 1}] {val}"
            return val
        if role == Qt.ItemDataRole.ForegroundRole and is_idx_col:
            return QColor("#4aa0ff")   # iReasoning-style blue
        if role == Qt.ItemDataRole.FontRole and is_idx_col:
            f = QFont()
            f.setItalic(True)
            return f
        return None

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        is_idx_header = (o == Qt.Orientation.Horizontal
                         and not self._rotated
                         and s == len(self._columns) - 1
                         and self._columns[s] == self.INDEX_VALUE_LABEL)
        if role == Qt.ItemDataRole.DisplayRole:
            if o == Qt.Orientation.Horizontal:
                # Non-rotated: MIB column names (last is "Index Value").
                # Rotated: each grid column = one instance — we label by row
                # number (1, 2, 3, …). The raw OID suffix is carried in the
                # last data row (was the Index Value column pre-rotate).
                if self._rotated:
                    return str(s + 1)
                return self._columns[s] if s < len(self._columns) else ""
            # Vertical header:
            # Non-rotated: hide (empty) — row number is visible in the
            #   Index Value column with the "[N] suffix" format.
            # Rotated: MIB column name (or "Index Value" for the last row).
            if self._rotated:
                return self._row_index[s] if s < len(self._row_index) else ""
            return ""
        if role == Qt.ItemDataRole.ForegroundRole and is_idx_header:
            return QColor("#4aa0ff")
        if role == Qt.ItemDataRole.FontRole and is_idx_header:
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ItemDataRole.ToolTipRole:
            if o == Qt.Orientation.Vertical and not self._rotated:
                if s < len(self._row_index):
                    return f"index: {self._row_index[s]}"
            if o == Qt.Orientation.Horizontal and self._rotated:
                if s < len(self._row_index):
                    return f"index: {self._row_index[s]}"
        return None

    def set_data(self, rows: list[list[str]], row_index: list[str]) -> None:
        self.beginResetModel()
        self._rows = rows
        self._row_index = row_index
        self._rotated = False
        self.endResetModel()

    def rotate(self) -> None:
        """Swap rows and columns (instances ↔ MIB columns)."""
        if not self._rows:
            return
        new_cols = self._row_index
        new_row_index = self._columns
        height = len(self._rows)
        width = max((len(r) for r in self._rows), default=0)
        new_rows = [[self._rows[r][c] if c < len(self._rows[r]) else ""
                     for r in range(height)] for c in range(width)]
        self.beginResetModel()
        self._columns = list(new_cols)
        self._row_index = list(new_row_index)
        self._rows = new_rows
        self._rotated = not self._rotated
        self.endResetModel()


class TableViewTab(QWidget):
    def __init__(self, parent, tree: MibTree, agent: Agent, oid: tuple[int, ...]) -> None:
        super().__init__(parent)
        self.tree = tree
        self.agent = agent
        self.oid = oid
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh)
        self._active_threads: list = []

        # Resolve to a table / entry node so we know column names.
        node = tree.lookup_oid(oid)
        if node and node.is_table and node.children:
            entry = node.children[0]          # typically ifEntry etc.
        elif node and node.is_table_entry:
            entry = node
        else:
            entry = node
        self.entry = entry

        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)

        tb = QToolBar()
        v.addWidget(tb)
        refresh_b = QPushButton(_t("Refresh")); refresh_b.clicked.connect(self._refresh); tb.addWidget(refresh_b)
        rotate_b  = QPushButton(_t("Rotate"));  rotate_b.clicked.connect(self._rotate);   tb.addWidget(rotate_b)
        export_b  = QPushButton(_t("Export CSV")); export_b.clicked.connect(self._export); tb.addWidget(export_b)
        tb.addSeparator()
        tb.addWidget(QLabel(_t(" Poll (s): ")))
        self.poll_spin = QSpinBox(); self.poll_spin.setRange(0, 3600); self.poll_spin.setValue(0)
        self.poll_spin.valueChanged.connect(self._poll_changed)
        tb.addWidget(self.poll_spin)

        # Column list: children of entry that are accessible
        if self.entry and self.entry.children:
            cols = [c.name for c in self.entry.children]
        else:
            cols = ["value"]
        self.model = TableDataModel(cols)

        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        self.view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self.view.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.view, 1)

        self.status_label = QLabel(_t("—"))
        v.addWidget(self.status_label)

    def _poll_changed(self, secs: int) -> None:
        if secs > 0:
            self._poll_timer.start(secs * 1000)
        else:
            self._poll_timer.stop()

    def _rotate(self) -> None:
        self.model.rotate()

    def _refresh(self) -> None:
        workers.prune_threads(self._active_threads)
        node = self.entry if self.entry else None
        root_oid = node.oid if node else self.oid
        oid_text = "." + ".".join(str(p) for p in root_oid)
        self.status_label.setText(f"walking {oid_text} …")
        # Reach up to the main window for its shared progress bar.
        mw = self.window()
        begin = getattr(mw, "_begin_progress", lambda *a, **k: None)
        end = getattr(mw, "_end_progress", lambda *a, **k: None)
        advance = getattr(mw, "_advance_progress", lambda *a, **k: None)
        begin(f"Table walk: {oid_text}")

        self._vb_count = 0

        def on_progress(vb):
            self._vb_count += 1
            if self._vb_count % 10 == 0:
                advance(0, f"Table walk: {self._vb_count} varbinds…")
        def on_finished(vbs):
            self._populate(vbs, root_oid)
            if not vbs:
                msg = (f"0 rows under {oid_text} (agent returned no data "
                       f"in this subtree)")
            else:
                msg = f"{self.model.rowCount()} rows · {len(vbs)} varbinds"
            self.status_label.setText(msg)
            end(msg)
        def on_failed(msg):
            self.status_label.setText(f"error: {msg}")
            end(f"Table walk failed: {msg}")
            QMessageBox.warning(self, "Table", msg)

        # Parallel per-column walk via GETBULK when we know the entry/columns.
        # This keeps row membership consistent on churning live tables
        # (timeWait TCP connections, etc.) because all columns of a row are
        # fetched in the same round-trip.
        if self.entry and self.entry.children:
            col_oids = [c.oid for c in self.entry.children]
            t, _ = workers.run_op(
                self, snmp_ops.op_table_walk, on_finished, on_failed, on_progress,
                self.agent, col_oids,
            )
        else:
            t, _ = workers.run_op(
                self, snmp_ops.op_walk, on_finished, on_failed, on_progress,
                self.agent, root_oid,
            )
        self._active_threads.append(t)

    def _populate(self, vbs, root_oid: tuple[int, ...]) -> None:
        # Map: col_index (1..N) → column OID
        if not (self.entry and self.entry.children):
            # Fallback: one unnamed column + Index Value column
            rows = [[vb.display_value,
                     ".".join(str(p) for p in vb.oid[len(root_oid):])]
                    for vb in vbs]
            idx = [".".join(str(p) for p in vb.oid[len(root_oid):]) for vb in vbs]
            self.model.set_data(rows, idx)
            return

        col_oids = {c.oid: ci for ci, c in enumerate(self.entry.children)}
        col_by_name = {c.name: ci for ci, c in enumerate(self.entry.children)}
        ncols = len(self.entry.children)
        # Per-column INTEGER enum maps, so ifAdminStatus=1 renders as "up"
        # etc. Empty dict = no translation.
        col_enums: list[dict[int, str]] = [c.enum_values for c in self.entry.children]
        rows_by_suffix: dict[tuple[int, ...], list[str]] = {}
        for vb in vbs:
            for col_oid, ci in col_oids.items():
                if len(vb.oid) > len(col_oid) and vb.oid[:len(col_oid)] == col_oid:
                    suffix = vb.oid[len(col_oid):]
                    row = rows_by_suffix.setdefault(suffix, [""] * ncols)
                    val = vb.display_value
                    em = col_enums[ci]
                    if em and vb.type_name in ("Integer", "Integer32"):
                        try:
                            iv = int(val)
                            if iv in em:
                                val = em[iv]
                        except (TypeError, ValueError):
                            pass
                    row[ci] = val
                    break

        # Fill not-accessible index columns (like sysORIndex, ifIndex,
        # ipAdEntAddr) from the OID suffix. The agent doesn't send them but
        # they're encoded in the instance identifier.
        index_names = self.entry.indices or []
        implied = bool(getattr(self.entry, "implied_last_index", False))
        for suf, row in rows_by_suffix.items():
            parsed = parse_index_suffix(self.tree, index_names, suf, implied)
            for name, val in parsed.items():
                ci = col_by_name.get(name)
                if ci is not None and not row[ci]:
                    row[ci] = val

        rows = []
        idx_labels = []
        for suf, vals in sorted(rows_by_suffix.items()):
            # Append the raw suffix as the trailing Index Value column —
            # matches iReasoning's column of the same name.
            vals.append(".".join(str(p) for p in suf))
            rows.append(vals)
            idx_labels.append(".".join(str(p) for p in suf))
        self.model.set_data(rows, idx_labels)

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export table", "table.csv",
                                               "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["", *self.model._columns])
            for r, row in enumerate(self.model._rows):
                w.writerow([self.model._row_index[r] if r < len(self.model._row_index) else "", *row])

    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        super().closeEvent(event)
