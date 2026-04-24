"""Device Snapshot — one-click dashboard of a device's basic health.

A read-only view that fires a fixed set of GET/WALK queries against the
currently-selected agent and lays the result out in three panels:

  • Basic Info      — sysName, sysDescr, sysUpTime, sysContact, sysLocation,
                      sysObjectID (SNMPv2-MIB)
  • Interfaces      — ifTable excerpt (Index, Descr, Type, OperStatus, Speed)
  • System Resources — hrSystemProcesses, hrMemorySize, hrStorageTable,
                      hrProcessorLoad (HOST-RESOURCES-MIB; silently
                      omitted if the agent doesn't publish it)

Matches iReasoning's Device Snapshot (clip_image027.jpg).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from ..config import AppSettings
from ..i18n import _t
from .. import snmp_ops, workers


# Known scalar OIDs we pull in one GET.
_BASIC_SCALARS = [
    ("sysDescr",     (1, 3, 6, 1, 2, 1, 1, 1, 0)),
    ("sysObjectID",  (1, 3, 6, 1, 2, 1, 1, 2, 0)),
    ("sysUpTime",    (1, 3, 6, 1, 2, 1, 1, 3, 0)),
    ("sysContact",   (1, 3, 6, 1, 2, 1, 1, 4, 0)),
    ("sysName",      (1, 3, 6, 1, 2, 1, 1, 5, 0)),
    ("sysLocation",  (1, 3, 6, 1, 2, 1, 1, 6, 0)),
    ("sysServices",  (1, 3, 6, 1, 2, 1, 1, 7, 0)),
]

# ifTable columns we walk: ifIndex (1), ifDescr (2), ifType (3),
# ifSpeed (5), ifOperStatus (8). We walk each column separately so
# partial ifTable implementations still give us what they have.
_IF_COLUMNS = [
    ("Index",     (1, 3, 6, 1, 2, 1, 2, 2, 1, 1)),
    ("Descr",     (1, 3, 6, 1, 2, 1, 2, 2, 1, 2)),
    ("Type",      (1, 3, 6, 1, 2, 1, 2, 2, 1, 3)),
    ("Speed",     (1, 3, 6, 1, 2, 1, 2, 2, 1, 5)),
    ("OperStatus",(1, 3, 6, 1, 2, 1, 2, 2, 1, 8)),
]

# HOST-RESOURCES-MIB scalars we try. Agents without it just return
# NoSuchObject which we skip.
_HR_SCALARS = [
    ("hrSystemProcesses", (1, 3, 6, 1, 2, 1, 25, 1, 6, 0)),
    ("hrMemorySize",      (1, 3, 6, 1, 2, 1, 25, 2, 2, 0)),
]


class DeviceSnapshotTab(QWidget):
    def __init__(self, parent, settings: AppSettings, current_agent_fn,
                 tree=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._current_agent_fn = current_agent_fn
        self.tree = tree
        self._active_threads: list = []

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)

        tb = QToolBar()
        refresh_b = QPushButton(_t("Refresh"))
        refresh_b.clicked.connect(self.refresh)
        tb.addWidget(refresh_b)
        tb.addSeparator()
        agent = self._current_agent_fn()
        tb.addWidget(QLabel(
            _t("Target") + f": {agent.host}:{agent.port} · v{agent.version}"))
        v.addWidget(tb)

        # --- Basic Info panel ---
        v.addWidget(self._section_label(_t("Basic Information")))
        self.basic_tbl = QTableWidget(0, 2)
        self.basic_tbl.setHorizontalHeaderLabels(["", ""])
        self.basic_tbl.horizontalHeader().setVisible(False)
        self.basic_tbl.verticalHeader().setVisible(False)
        self.basic_tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.basic_tbl.setShowGrid(False)
        self.basic_tbl.setAlternatingRowColors(True)
        self.basic_tbl.setMaximumHeight(200)
        self.basic_tbl.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.basic_tbl)

        # --- Interfaces panel ---
        v.addWidget(self._section_label(_t("Interface Information")))
        self.if_tbl = QTableWidget(0, len(_IF_COLUMNS))
        self.if_tbl.setHorizontalHeaderLabels([c[0] for c in _IF_COLUMNS])
        self.if_tbl.verticalHeader().setVisible(False)
        self.if_tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.if_tbl.setAlternatingRowColors(True)
        self.if_tbl.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.if_tbl, 1)

        # --- Resources panel ---
        v.addWidget(self._section_label(_t("System Resources")))
        self.hr_tbl = QTableWidget(0, 2)
        self.hr_tbl.setHorizontalHeaderLabels(["", ""])
        self.hr_tbl.horizontalHeader().setVisible(False)
        self.hr_tbl.verticalHeader().setVisible(False)
        self.hr_tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.hr_tbl.setShowGrid(False)
        self.hr_tbl.setMaximumHeight(120)
        self.hr_tbl.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.hr_tbl)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #666;")
        v.addWidget(self.status)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "QLabel { background: #2c6aa0; color: white; "
            "padding: 4px 8px; font-weight: bold; }")
        return lbl

    # --- queries ------------------------------------------------------

    def refresh(self) -> None:
        workers.prune_threads(self._active_threads)
        agent = self._current_agent_fn()
        self.status.setText(
            _t("Fetching from {host}:{port}…").format(
                host=agent.host, port=agent.port))
        # 1. Basic scalars — one GET batch.
        def on_basic_done(vbs):
            self._fill_keyvalue(self.basic_tbl, vbs, _BASIC_SCALARS)
        def on_basic_fail(msg):
            self._fill_keyvalue(self.basic_tbl, [], _BASIC_SCALARS)
            self.status.setText(_t("Error: {msg}").format(msg=msg))
        oids = [oid for _n, oid in _BASIC_SCALARS]
        t, _w = workers.run_op(
            self, snmp_ops.op_get, on_basic_done, on_basic_fail,
            None, agent, oids)
        self._active_threads.append(t)

        # 2. HR scalars — separate batch so they don't cascade-fail the basics.
        def on_hr_done(vbs):
            self._fill_keyvalue(self.hr_tbl, vbs, _HR_SCALARS)
        def on_hr_fail(_m):
            self._fill_keyvalue(self.hr_tbl, [], _HR_SCALARS)
        hr_oids = [oid for _n, oid in _HR_SCALARS]
        t2, _w2 = workers.run_op(
            self, snmp_ops.op_get, on_hr_done, on_hr_fail,
            None, agent, hr_oids)
        self._active_threads.append(t2)

        # 3. ifTable walk — one WALK per column.
        self.if_tbl.setRowCount(0)
        # Prepare a dict keyed by ifIndex → per-column values.
        self._if_rows: dict[tuple, dict[str, str]] = {}
        self._if_pending = len(_IF_COLUMNS)
        for cname, coid in _IF_COLUMNS:
            self._walk_column(agent, cname, coid)

    def _walk_column(self, agent, cname: str,
                     coid: tuple[int, ...]) -> None:
        def on_prog(vb):
            suffix = tuple(vb.oid[len(coid):])
            self._if_rows.setdefault(suffix, {})[cname] = vb.display_value
        def on_done(_vbs):
            self._if_pending -= 1
            if self._if_pending == 0:
                self._render_ifs()
        def on_fail(_msg):
            self._if_pending -= 1
            if self._if_pending == 0:
                self._render_ifs()
        t, _w = workers.run_op(
            self, snmp_ops.op_walk, on_done, on_fail, on_prog,
            agent, coid)
        self._active_threads.append(t)

    def _render_ifs(self) -> None:
        rows = sorted(self._if_rows.items(), key=lambda kv: kv[0])
        self.if_tbl.setRowCount(len(rows))
        for r, (suffix, cols) in enumerate(rows):
            for c, (cname, _coid) in enumerate(_IF_COLUMNS):
                val = cols.get(cname, "")
                item = QTableWidgetItem(val)
                # Color OperStatus: up=green / down=red.
                if cname == "OperStatus" and val:
                    lv = val.lower()
                    if "up" in lv:
                        item.setForeground(Qt.GlobalColor.darkGreen)
                    elif "down" in lv:
                        item.setForeground(Qt.GlobalColor.darkRed)
                self.if_tbl.setItem(r, c, item)
        self.if_tbl.resizeColumnsToContents()
        self.if_tbl.horizontalHeader().setStretchLastSection(True)
        agent = self._current_agent_fn()
        self.status.setText(_t(
            "Snapshot from {host}:{port} · {rows} interfaces"
        ).format(host=agent.host, port=agent.port, rows=len(rows)))

    def _fill_keyvalue(self, tbl: QTableWidget, vbs,
                        names: list[tuple[str, tuple[int, ...]]]) -> None:
        # Map returned OIDs back to the labelled names (match by prefix).
        oid_to_name = {oid: n for n, oid in names}
        data = {}
        for vb in vbs:
            key = tuple(vb.oid)
            if key in oid_to_name:
                v = vb.display_value
                # Skip NoSuchObject/NoSuchInstance markers to keep the
                # panel uncluttered — iReasoning hides these too.
                if vb.type_name in ("NoSuchObject", "NoSuchInstance",
                                     "EndOfMibView"):
                    continue
                data[oid_to_name[key]] = str(v)
        shown = [(n, data[n]) for n, _ in names if n in data]
        tbl.setRowCount(len(shown))
        for r, (n, v) in enumerate(shown):
            key_item = QTableWidgetItem(n + ":")
            f = key_item.font(); f.setBold(True); key_item.setFont(f)
            tbl.setItem(r, 0, key_item)
            tbl.setItem(r, 1, QTableWidgetItem(v))
        tbl.resizeColumnsToContents()
        tbl.horizontalHeader().setStretchLastSection(True)
