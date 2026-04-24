"""Create / edit a Poll definition — mirrors iReasoning's Create Poll.

Layout:
  Poll Name:   [________________]
  Interval:    [___] seconds
  SNMP Agents: [list]                [Add Agent]  [Clear Agents]
  ─── Variables to poll ───
  [Add Variable]  [Modify]  [Delete]
  +──────────────┬──────────────┬───────────+
  │Variable Name │Variable OID  │Operation  │
  +──────────────┴──────────────┴───────────+
                           [ Ok ] [ Cancel ]
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from ..config import Agent, AppSettings, PollDefinition, PollVariable
from ..i18n import _t


OP_CHOICES = ("Get", "Get Next")


def _agent_key(a: Agent) -> str:
    return f"{a.host}:{a.port}"


class VariableDialog(QDialog):
    """Sub-dialog: edit one PollVariable."""

    def __init__(self, seed: PollVariable, tree=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Poll Variable"))
        self.setMinimumWidth(420)
        self.tree = tree
        self.result_var = PollVariable(**vars(seed))

        form = QFormLayout(self)
        self.name_edit = QLineEdit(seed.name)
        self.oid_edit = QLineEdit(seed.oid)
        self.op_combo = QComboBox(); self.op_combo.addItems(OP_CHOICES)
        self.op_combo.setCurrentText(seed.operation or "Get")
        form.addRow(_t("Variable Name") + ":", self.name_edit)
        form.addRow(_t("Variable OID") + ":", self.oid_edit)
        form.addRow(_t("Operation") + ":", self.op_combo)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _accept(self) -> None:
        name = self.name_edit.text().strip()
        oid = self.oid_edit.text().strip()
        if not oid:
            QMessageBox.warning(self, _t("Poll Variable"),
                                _t("OID is required."))
            return
        # Try to resolve name if user typed a symbolic OID — lets you put
        # "sysUpTime" in the OID field and auto-fills name.
        if self.tree is not None:
            resolved = self.tree.resolve_name(oid)
            if resolved is None:
                QMessageBox.warning(self, _t("Poll Variable"),
                                    _t("Cannot resolve OID"))
                return
            if not name:
                name = oid  # keep symbolic label
            # Persist OID as dotted numeric so runtime doesn't re-resolve.
            oid = "." + ".".join(str(x) for x in resolved)
        if not name:
            name = oid
        self.result_var = PollVariable(
            name=name, oid=oid,
            operation=self.op_combo.currentText())
        self.accept()


class PollDialog(QDialog):
    """Create Poll / Edit Poll — fills a PollDefinition."""

    def __init__(self, seed: Optional[PollDefinition], settings: AppSettings,
                 tree=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Create Poll") if seed is None
                             else _t("Edit Poll"))
        self.resize(560, 480)
        self.settings = settings
        self.tree = tree
        self.result_poll = PollDefinition(**vars(seed)) if seed else PollDefinition()

        v = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit(self.result_poll.name)
        self.interval_edit = QSpinBox()
        self.interval_edit.setRange(1, 3600)
        self.interval_edit.setValue(self.result_poll.interval_s or 30)
        self.interval_edit.setSuffix(" " + _t("seconds"))
        form.addRow(_t("Poll Name") + ":", self.name_edit)
        form.addRow(_t("Interval") + ":", self.interval_edit)
        v.addLayout(form)

        # --- Agents list -------------------------------------------------
        v.addWidget(QLabel(_t("SNMP Agents") + ":"))
        ag_row = QHBoxLayout()
        self.ag_list = QListWidget()
        self.ag_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        for key in self.result_poll.agents:
            QListWidgetItem(key, self.ag_list)
        ag_row.addWidget(self.ag_list, 1)
        ag_btns = QVBoxLayout()
        add_ag = QPushButton(_t("Add Agent"))
        add_ag.clicked.connect(self._add_agent)
        rm_ag = QPushButton(_t("Remove Agent"))
        rm_ag.clicked.connect(self._remove_agent)
        clr_ag = QPushButton(_t("Clear Agents"))
        clr_ag.clicked.connect(lambda: self.ag_list.clear())
        for b in (add_ag, rm_ag, clr_ag):
            ag_btns.addWidget(b)
        ag_btns.addStretch()
        ag_row.addLayout(ag_btns)
        v.addLayout(ag_row, 1)

        # --- Variables table --------------------------------------------
        v.addWidget(QLabel(_t("Variables to poll") + ":"))
        var_btn_row = QHBoxLayout()
        add_var = QPushButton(_t("Add Variable"))
        add_var.clicked.connect(self._add_variable)
        mod_var = QPushButton(_t("Modify"))
        mod_var.clicked.connect(self._modify_variable)
        del_var = QPushButton(_t("Delete"))
        del_var.clicked.connect(self._delete_variable)
        for b in (add_var, mod_var, del_var):
            var_btn_row.addWidget(b)
        var_btn_row.addStretch()
        v.addLayout(var_btn_row)

        self.var_tbl = QTableWidget(0, 3)
        self.var_tbl.setHorizontalHeaderLabels(
            [_t("Variable Name"), _t("Variable OID"), _t("Operation")])
        self.var_tbl.verticalHeader().setVisible(False)
        self.var_tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.var_tbl.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.var_tbl.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.var_tbl.doubleClicked.connect(lambda _=None: self._modify_variable())
        self.var_tbl.horizontalHeader().setStretchLastSection(True)
        self._refill_vars()
        v.addWidget(self.var_tbl, 2)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    # ------------------------------------------------------------------

    def _refill_vars(self, select: int = -1) -> None:
        self.var_tbl.setRowCount(len(self.result_poll.variables))
        for r, pv in enumerate(self.result_poll.variables):
            self.var_tbl.setItem(r, 0, QTableWidgetItem(pv.name))
            self.var_tbl.setItem(r, 1, QTableWidgetItem(pv.oid))
            self.var_tbl.setItem(r, 2, QTableWidgetItem(pv.operation))
        self.var_tbl.resizeColumnsToContents()
        self.var_tbl.horizontalHeader().setStretchLastSection(True)
        if 0 <= select < len(self.result_poll.variables):
            self.var_tbl.selectRow(select)

    def _selected_var(self) -> int:
        r = self.var_tbl.currentRow()
        return r if 0 <= r < len(self.result_poll.variables) else -1

    # --- agents -------------------------------------------------------

    def _add_agent(self) -> None:
        """Offer saved + current agents; user picks one (or types). Keeps
        storage as host:port strings."""
        existing = {self.ag_list.item(i).text()
                    for i in range(self.ag_list.count())}
        choices = [_agent_key(self.settings.current_agent)] + [
            _agent_key(a) for a in self.settings.saved_agents
        ]
        # Dedup preserving order.
        seen: set[str] = set()
        options = []
        for c in choices:
            if c in seen or c in existing:
                continue
            seen.add(c)
            options.append(c)
        # Let the user pick from the list OR type a new host:port.
        text, ok = QInputDialog.getItem(
            self, _t("Add Agent"),
            _t("Agent (host:port):"),
            options or [""], 0, True)
        if not ok:
            return
        text = (text or "").strip()
        if not text:
            return
        if text in existing:
            return
        QListWidgetItem(text, self.ag_list)

    def _remove_agent(self) -> None:
        for item in self.ag_list.selectedItems():
            self.ag_list.takeItem(self.ag_list.row(item))

    # --- variables ----------------------------------------------------

    def _add_variable(self) -> None:
        d = VariableDialog(PollVariable(), tree=self.tree, parent=self)
        if d.exec():
            self.result_poll.variables.append(d.result_var)
            self._refill_vars(select=len(self.result_poll.variables) - 1)

    def _modify_variable(self) -> None:
        r = self._selected_var()
        if r < 0:
            return
        d = VariableDialog(self.result_poll.variables[r],
                           tree=self.tree, parent=self)
        if d.exec():
            self.result_poll.variables[r] = d.result_var
            self._refill_vars(select=r)

    def _delete_variable(self) -> None:
        r = self._selected_var()
        if r < 0:
            return
        del self.result_poll.variables[r]
        self._refill_vars()

    # --- accept --------------------------------------------------------

    def _accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, _t("Create Poll"),
                                _t("Poll Name is required."))
            return
        agents = [self.ag_list.item(i).text()
                  for i in range(self.ag_list.count())]
        if not agents:
            QMessageBox.warning(self, _t("Create Poll"),
                                _t("At least one agent is required."))
            return
        if not self.result_poll.variables:
            QMessageBox.warning(self, _t("Create Poll"),
                                _t("Add at least one variable to poll."))
            return
        self.result_poll.name = name
        self.result_poll.interval_s = int(self.interval_edit.value())
        self.result_poll.agents = agents
        self.accept()
