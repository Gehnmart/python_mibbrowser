"""Agent-book: saved SNMP agent profiles.

The table is a read-only view; every editing action (Add, Edit, double-click,
Duplicate) routes through AgentDialog — the single full editor that also
handles SNMPv3 fields. Keeps one mental model for "editing an agent".
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..config import Agent, AppSettings
from ..i18n import _t


COLUMNS = ("Host", "Port", "Version", "Read community", "Write community",
           "Timeout", "Retries")


class AgentsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Manage agents"))
        self.resize(720, 420)
        self.settings = settings
        self.changed = False
        self.chosen_index: int | None = None

        v = QVBoxLayout(self)

        self.tbl = QTableWidget(0, len(COLUMNS))
        self.tbl.setHorizontalHeaderLabels([_t(c) for c in COLUMNS])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(self.tbl.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(self.tbl.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(self.tbl.SelectionMode.SingleSelection)
        self.tbl.doubleClicked.connect(lambda _=None: self._edit())
        # Stretch numeric-ish narrow columns to content, let Host eat the
        # leftover width so the table always fills the dialog.
        from PyQt6.QtWidgets import QHeaderView
        hdr = self.tbl.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(COLUMNS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.tbl, 1)

        hb = QHBoxLayout()
        add_b = QPushButton(_t("Add")); add_b.clicked.connect(self._add)
        hb.addWidget(add_b)
        edit_b = QPushButton(_t("Edit…")); edit_b.clicked.connect(self._edit)
        hb.addWidget(edit_b)
        dup_b = QPushButton(_t("Duplicate")); dup_b.clicked.connect(self._duplicate)
        hb.addWidget(dup_b)
        rm_b = QPushButton(_t("Remove")); rm_b.clicked.connect(self._remove)
        hb.addWidget(rm_b)
        hb.addStretch()
        use_b = QPushButton(_t("Use selected"))
        use_b.setDefault(True)
        use_b.clicked.connect(self._use_selected)
        hb.addWidget(use_b)
        v.addLayout(hb)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        # Qt's stock Close button label isn't run through our i18n — set it.
        btns.button(QDialogButtonBox.StandardButton.Close).setText(_t("Close"))
        btns.rejected.connect(self.accept)
        btns.accepted.connect(self.accept)
        v.addWidget(btns)

        self._populate()

    # ------------------------------------------------------------------

    def _populate(self, select: int = -1) -> None:
        self.tbl.setRowCount(len(self.settings.saved_agents))
        for r, ag in enumerate(self.settings.saved_agents):
            for c, val in enumerate((ag.host, str(ag.port), ag.version,
                                     ag.read_community, ag.write_community,
                                     str(ag.timeout_s), str(ag.retries))):
                self.tbl.setItem(r, c, QTableWidgetItem(val))
        self.tbl.resizeColumnsToContents()
        if 0 <= select < len(self.settings.saved_agents):
            self.tbl.selectRow(select)

    def _selected_index(self) -> int:
        r = self.tbl.currentRow()
        return r if 0 <= r < len(self.settings.saved_agents) else -1

    def _open_editor(self, seed: Agent) -> Agent | None:
        from .dialogs import AgentDialog
        d = AgentDialog(seed, self)
        if d.exec():
            return d.agent
        return None

    def _mark_changed(self) -> None:
        self.changed = True
        self.settings.save()

    # --- actions ------------------------------------------------------

    def _add(self) -> None:
        # Seed with the defaults template and let the user adjust in the
        # full editor. Cancel = no append.
        template = self.settings.default_agent
        new = self._open_editor(Agent(**vars(template)))
        if new is None:
            return
        self.settings.saved_agents.append(new)
        self._mark_changed()
        self._populate(select=len(self.settings.saved_agents) - 1)

    def _edit(self) -> None:
        r = self._selected_index()
        if r < 0:
            return
        edited = self._open_editor(self.settings.saved_agents[r])
        if edited is None:
            return
        self.settings.saved_agents[r] = edited
        self._mark_changed()
        self._populate(select=r)

    def _duplicate(self) -> None:
        r = self._selected_index()
        if r < 0:
            return
        src = self.settings.saved_agents[r]
        self.settings.saved_agents.insert(r + 1, Agent(**vars(src)))
        self._mark_changed()
        self._populate(select=r + 1)

    def _remove(self) -> None:
        r = self._selected_index()
        if r < 0:
            return
        name = self.settings.saved_agents[r].host
        btn = QMessageBox.question(
            self, _t("Manage agents"),
            _t("Remove agent '{host}' from the saved list?").format(host=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if btn != QMessageBox.StandardButton.Yes:
            return
        del self.settings.saved_agents[r]
        self._mark_changed()
        self._populate(select=max(0, r - 1))

    def _use_selected(self) -> None:
        r = self._selected_index()
        if r < 0:
            return
        self.chosen_index = r
        self.accept()
