"""Live Poll tab — periodic GET across N agents × M variables.

Rows = agents, columns = variables (as configured in PollDefinition).
Refresh button forces a poll; the interval timer does the same on a
schedule. Matches iReasoning's Poll Result tab layout.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import snmp_ops, workers
from ..config import Agent, AppSettings, PollDefinition
from ..i18n import _t


def _agent_key(a: Agent) -> str:
    return f"{a.host}:{a.port}"


class PollTab(QWidget):
    def __init__(self, parent, poll: PollDefinition, settings: AppSettings,
                 tree=None) -> None:
        super().__init__(parent)
        self.poll = poll
        self.settings = settings
        self.tree = tree
        self._paused = False
        self._active_threads: list = []

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_now)

        self._build_ui()
        self._poll_now()
        self._timer.start(max(1, poll.interval_s) * 1000)

    # UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)

        tb = QToolBar()
        refresh_b = QPushButton(_t("Refresh"))
        refresh_b.clicked.connect(self._poll_now)
        tb.addWidget(refresh_b)
        self.pause_btn = QPushButton(_t("⏸ Pause"))
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._toggle_pause)
        tb.addWidget(self.pause_btn)
        export_b = QPushButton(_t("Export CSV"))
        export_b.clicked.connect(self._export_csv)
        tb.addWidget(export_b)
        tb.addSeparator()
        tb.addWidget(QLabel(
            _t("Interval:") + f" {self.poll.interval_s}s · "
            f"{len(self.poll.agents)} × {len(self.poll.variables)}"))
        v.addWidget(tb)

        self.tbl = QTableWidget(
            len(self.poll.agents), 1 + len(self.poll.variables))
        headers = [_t("Agent")] + [pv.name for pv in self.poll.variables]
        self.tbl.setHorizontalHeaderLabels(headers)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        for r, key in enumerate(self.poll.agents):
            self.tbl.setItem(r, 0, QTableWidgetItem(key))
            for c in range(1, len(headers)):
                self.tbl.setItem(r, c, QTableWidgetItem(""))
        self.tbl.resizeColumnsToContents()
        v.addWidget(self.tbl, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666;")
        v.addWidget(self.status_label)

    # Control ----------------------------------------------------------

    def _toggle_pause(self, checked: bool) -> None:
        self._paused = checked
        self.pause_btn.setText(
            _t("▶ Resume") if checked else _t("⏸ Pause"))
        if checked:
            self._timer.stop()
        else:
            self._timer.start(max(1, self.poll.interval_s) * 1000)
            self._poll_now()

    # Lookup an Agent object by "host:port" key — used to map poll.agents
    # strings back to the full agent config (timeouts, community, …).
    def _resolve_agent(self, key: str) -> Agent | None:
        candidates = [self.settings.current_agent, *self.settings.saved_agents]
        for a in candidates:
            if _agent_key(a) == key:
                return a
        # Fall back: parse the key as host[:port] and inherit everything
        # else from the default template. Means a poll survives the
        # referenced agent being deleted from Manage agents.
        host, _, port_s = key.partition(":")
        ag = Agent(**vars(self.settings.default_agent))
        ag.host = host or "127.0.0.1"
        try:
            ag.port = int(port_s) if port_s else 161
        except ValueError:
            ag.port = 161
        return ag

    # Polling ----------------------------------------------------------

    def _poll_now(self) -> None:
        if self._paused:
            return
        workers.prune_threads(self._active_threads)
        resolved_oids: list[tuple[int, ...]] = []
        for pv in self.poll.variables:
            if self.tree is not None:
                t = self.tree.resolve_name(pv.oid)
                if t is None:
                    try:
                        t = tuple(int(p) for p in pv.oid.strip(".").split("."))
                    except ValueError:
                        t = ()
            else:
                try:
                    t = tuple(int(p) for p in pv.oid.strip(".").split("."))
                except ValueError:
                    t = ()
            resolved_oids.append(t)

        for r, key in enumerate(self.poll.agents):
            agent = self._resolve_agent(key)
            if agent is None:
                continue
            for c, (pv, oid) in enumerate(
                    zip(self.poll.variables, resolved_oids, strict=False), start=1):
                if not oid:
                    self.tbl.item(r, c).setText("?")
                    continue
                self._dispatch(r, c, agent, pv.operation, oid)

        self.status_label.setText(
            _t("Polling {n} agent(s)…").format(n=len(self.poll.agents)))

    def _dispatch(self, row: int, col: int, agent: Agent,
                   op: str, oid: tuple[int, ...]) -> None:
        fn = (snmp_ops.op_next if op == "Get Next" else snmp_ops.op_get)

        def on_finished(vbs):
            if not vbs:
                self._set_cell(row, col, "—", error=True)
                return
            vb = vbs[0]
            self._set_cell(row, col, vb.display_value)

        def on_failed(msg):
            self._set_cell(row, col, f"error: {msg[:40]}", error=True)

        t, _w = workers.run_op(
            self, fn, on_finished, on_failed, None, agent, [oid])
        self._active_threads.append(t)

    def _set_cell(self, row: int, col: int, text: str, error: bool = False) -> None:
        item = self.tbl.item(row, col)
        if item is None:
            return
        item.setText(text)
        if error:
            item.setForeground(Qt.GlobalColor.darkRed)
        else:
            item.setForeground(Qt.GlobalColor.black)

    # Export -----------------------------------------------------------

    def _export_csv(self) -> None:
        import csv
        path, _sel = QFileDialog.getSaveFileName(
            self, _t("Export CSV"),
            f"{self.poll.name or 'poll'}.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            headers = [self.tbl.horizontalHeaderItem(c).text()
                       for c in range(self.tbl.columnCount())]
            w.writerow(headers)
            for r in range(self.tbl.rowCount()):
                w.writerow([self.tbl.item(r, c).text() if self.tbl.item(r, c)
                            else "" for c in range(self.tbl.columnCount())])

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
