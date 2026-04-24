"""Trap Receiver window: start/stop listener, show traps, rules + filter."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (QAbstractTableModel, QModelIndex, QObject, Qt,
                          QSortFilterProxyModel, pyqtSignal)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QSplitter, QStatusBar, QTableView, QTextBrowser,
    QToolBar, QVBoxLayout, QWidget,
)

from .. import config
from ..mib_loader import MibTree
from ..trap_receiver import TrapEvent, TrapListener


@dataclass
class TrapRule:
    name: str = "new-rule"
    match_oid: str = "*"            # wildcard
    allow_ips: str = "*"             # comma-sep wildcards
    payload_contains: str = ""        # | or , separated keywords
    action: str = "accept"           # accept / ignore
    set_severity: str = ""           # "" keeps existing
    set_message: str = ""            # format str, {oid} etc.


def _wild_to_re(pat: str) -> re.Pattern:
    """Translate '*'/'?' wildcards to regex. '*' matches any digits and dots
    (to let '.1.3.6.4.155.12.*' match any OID in the subtree)."""
    regex = "^"
    for c in pat:
        if c == "*":
            regex += r"[\d.]*"
        elif c == "?":
            regex += r"[\d]?"
        else:
            regex += re.escape(c)
    regex += "$"
    return re.compile(regex)


def _rule_matches(rule: TrapRule, ev: TrapEvent) -> bool:
    if rule.match_oid and rule.match_oid != "*":
        if not _wild_to_re(rule.match_oid).match(ev.trap_oid):
            return False
    if rule.allow_ips and rule.allow_ips != "*":
        parts = [p.strip() for p in rule.allow_ips.split(",") if p.strip()]
        if parts and not any(_wild_to_re(p).match(ev.source_ip) for p in parts):
            return False
    if rule.payload_contains:
        needles = [s.strip() for s in re.split(r"[|,]", rule.payload_contains) if s.strip()]
        hay = " ".join(v for _, _, v in ev.var_binds) + " " + ev.trap_oid
        if not any(n in hay for n in needles):
            return False
    return True


# ---------------------------------------------------------------------------
# Table model
# ---------------------------------------------------------------------------

TRAP_COLS = ("Time", "Source", "Severity", "Trap OID", "Name", "Community", "Message")


class TrapTableModel(QAbstractTableModel):
    def __init__(self, tree: MibTree) -> None:
        super().__init__()
        self.tree = tree
        self._rows: list[TrapEvent] = []

    def add(self, ev: TrapEvent) -> None:
        self.beginInsertRows(QModelIndex(), 0, 0)
        self._rows.insert(0, ev)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int: return len(self._rows)
    def columnCount(self, parent=QModelIndex()) -> int: return len(TRAP_COLS)

    def data(self, idx: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not idx.isValid():
            return None
        ev = self._rows[idx.row()]
        col = idx.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0: return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ev.time))
            if col == 1: return f"{ev.source_ip}:{ev.source_port}"
            if col == 2: return ev.severity
            if col == 3: return ev.trap_oid
            if col == 4:
                try:
                    oid_t = tuple(int(p) for p in ev.trap_oid.strip(".").split(".") if p)
                    return self.tree.format_oid(oid_t)
                except Exception:
                    return ""
            if col == 5: return ev.community
            if col == 6: return ev.message
        if role == Qt.ItemDataRole.ForegroundRole and col == 2:
            sv = (ev.severity or "").upper()
            if sv in ("CRITICAL", "HIGH"):   return QColor("red")
            if sv == "MEDIUM":               return QColor("#b38600")
            if sv == "LOW":                  return QColor("blue")
        return None

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and o == Qt.Orientation.Horizontal:
            return TRAP_COLS[s]
        return None

    def get(self, row: int) -> Optional[TrapEvent]:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None


# ---------------------------------------------------------------------------
# Signal bridge so the listener thread can emit into the Qt loop
# ---------------------------------------------------------------------------

class _TrapBridge(QObject):
    trap_received = pyqtSignal(object)   # TrapEvent


# ---------------------------------------------------------------------------
# Rule editor dialog
# ---------------------------------------------------------------------------

class TrapRuleDialog(QDialog):
    def __init__(self, rule: TrapRule, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Trap rule: {rule.name}")
        self.rule = TrapRule(**asdict(rule))
        form = QFormLayout(self)
        self.name_e   = QLineEdit(self.rule.name)
        self.oid_e    = QLineEdit(self.rule.match_oid)
        self.ips_e    = QLineEdit(self.rule.allow_ips)
        self.pay_e    = QLineEdit(self.rule.payload_contains)
        self.action_c = QComboBox(); self.action_c.addItems(("accept","ignore")); self.action_c.setCurrentText(self.rule.action)
        self.sev_c    = QComboBox(); self.sev_c.addItems(("", "INFO","LOW","MEDIUM","HIGH","CRITICAL")); self.sev_c.setCurrentText(self.rule.set_severity)
        self.msg_e    = QLineEdit(self.rule.set_message)
        form.addRow("Name", self.name_e)
        form.addRow("Trap OID matches", self.oid_e)
        form.addRow("Source IPs allowed", self.ips_e)
        form.addRow("Payload contains", self.pay_e)
        form.addRow("Action", self.action_c)
        form.addRow("Set severity", self.sev_c)
        form.addRow("Set message", self.msg_e)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _accept(self) -> None:
        self.rule.name = self.name_e.text().strip() or "unnamed"
        self.rule.match_oid = self.oid_e.text().strip()
        self.rule.allow_ips = self.ips_e.text().strip()
        self.rule.payload_contains = self.pay_e.text().strip()
        self.rule.action = self.action_c.currentText()
        self.rule.set_severity = self.sev_c.currentText()
        self.rule.set_message = self.msg_e.text()
        self.accept()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class TrapReceiverWindow(QMainWindow):
    def __init__(self, tree: MibTree, settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Trap Receiver")
        self.resize(1000, 640)
        self.tree = tree
        self.settings = settings
        self.listener: Optional[TrapListener] = None
        self.rules: list[TrapRule] = self._load_rules()
        self._rules_ignored_count = 0

        # Bridge for cross-thread
        self.bridge = _TrapBridge()
        self.bridge.trap_received.connect(self._handle_trap)

        self._build_ui()

    # Persistence ------------------------------------------------------

    @staticmethod
    def _rules_path() -> Path:
        return config.config_dir() / "trap_rules.json"

    def _load_rules(self) -> list[TrapRule]:
        p = self._rules_path()
        if not p.exists():
            return []
        try:
            return [TrapRule(**d) for d in json.loads(p.read_text())]
        except Exception:
            return []

    def _save_rules(self) -> None:
        p = self._rules_path()
        p.write_text(json.dumps([asdict(r) for r in self.rules], indent=2))

    # UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        tb = QToolBar("Main")
        self.addToolBar(tb)

        self.start_btn = QPushButton("▶ Start")
        self.start_btn.clicked.connect(self._start)
        tb.addWidget(self.start_btn)

        self.stop_btn = QPushButton("⏸ Stop")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        tb.addWidget(self.stop_btn)

        tb.addSeparator()
        tb.addWidget(QLabel(" Port: "))
        self.port_edit = QLineEdit(str(self.settings.trap_port))
        self.port_edit.setFixedWidth(70)
        tb.addWidget(self.port_edit)

        tb.addSeparator()
        tb.addWidget(QLabel(" Filter: "))
        self.filter_edit = QLineEdit()
        self.filter_edit.setMinimumWidth(220)
        tb.addWidget(self.filter_edit)

        tb.addSeparator()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear)
        tb.addWidget(clear_btn)

        rules_btn = QPushButton("Rules…")
        rules_btn.clicked.connect(self._open_rules)
        tb.addWidget(rules_btn)

        save_btn = QPushButton("Save…")
        save_btn.clicked.connect(self._save_to_file)
        tb.addWidget(save_btn)

        # Split: table + details
        split = QSplitter(Qt.Orientation.Vertical)
        self.model = TrapTableModel(self.tree)
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.filter_edit.textChanged.connect(self.proxy.setFilterFixedString)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.clicked.connect(self._on_select)
        split.addWidget(self.table)

        self.details = QTextBrowser()
        self.details.setReadOnly(True)
        split.addWidget(self.details)
        split.setSizes([380, 220])
        self.setCentralWidget(split)

        sb = QStatusBar()
        self.setStatusBar(sb)
        sb.showMessage("Not listening.")

    # Event handling ---------------------------------------------------

    def _start(self) -> None:
        try:
            port = int(self.port_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Trap", "Invalid port.")
            return
        self.settings.trap_port = port
        self.listener = TrapListener(port=port,
                                     on_trap=self.bridge.trap_received.emit)
        try:
            self.listener.start()
        except PermissionError as exc:
            QMessageBox.critical(self, "Trap", str(exc) + "\n\nTip: run as root "
                                 "or choose a port >= 1024.")
            self.listener = None
            return
        except OSError as exc:
            QMessageBox.critical(self, "Trap", f"Cannot listen: {exc}")
            self.listener = None
            return
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.port_edit.setEnabled(False)
        self.statusBar().showMessage(f"Listening on 0.0.0.0:{port}")

    def _stop(self) -> None:
        if self.listener is not None:
            self.listener.stop()
            self.listener = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.port_edit.setEnabled(True)
        self.statusBar().showMessage("Stopped.")

    def _handle_trap(self, ev: TrapEvent) -> None:
        # Apply rules
        ignored = False
        for rule in self.rules:
            if not _rule_matches(rule, ev):
                continue
            if rule.action == "ignore":
                ignored = True
                continue
            if rule.set_severity:
                ev.severity = rule.set_severity
            if rule.set_message:
                try:
                    ev.message = rule.set_message.format(
                        oid=ev.trap_oid, ip=ev.source_ip,
                        community=ev.community)
                except Exception:
                    ev.message = rule.set_message
        if ignored:
            self._rules_ignored_count += 1
            return
        self.model.add(ev)

    def _on_select(self, idx: QModelIndex) -> None:
        src = self.proxy.mapToSource(idx)
        ev = self.model.get(src.row())
        if ev is None:
            return
        parts = [
            f"<h3>Trap {ev.trap_oid}</h3>",
            f"<b>Time:</b> {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ev.time))}<br>",
            f"<b>From:</b> {ev.source_ip}:{ev.source_port}<br>",
            f"<b>Version:</b> SNMP {ev.version}, community: {ev.community}<br>",
            f"<b>Uptime:</b> {ev.uptime}<br>",
            f"<b>Severity:</b> {ev.severity}<br>",
        ]
        if ev.version == "1":
            parts.append(f"<b>Enterprise:</b> {ev.enterprise}<br>")
            parts.append(f"<b>Generic/Specific:</b> {ev.generic_trap}/{ev.specific_trap}<br>")
        parts.append("<br><b>Variable bindings:</b><br><ul>")
        for oid, tn, val in ev.var_binds:
            try:
                oid_t = tuple(int(p) for p in oid.split(".") if p)
                name = self.tree.format_oid(oid_t)
            except Exception:
                name = oid
            parts.append(f"<li><tt>{name}</tt> ({tn}): {val}</li>")
        parts.append("</ul>")
        if ev.message:
            parts.append(f"<br><b>Message:</b> {ev.message}")
        self.details.setHtml("".join(parts))

    def _clear(self) -> None:
        self.model.clear()
        self.details.clear()

    def _save_to_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save traps", "traps.json",
                                              "JSON (*.json)")
        if not path:
            return
        data = []
        for ev in self.model._rows:
            d = asdict(ev)
            data.append(d)
        Path(path).write_text(json.dumps(data, indent=2))

    def _open_rules(self) -> None:
        # Simple list dialog: add/edit/remove.
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QHBoxLayout, QVBoxLayout, QPushButton, QDialog
        d = QDialog(self); d.setWindowTitle("Trap rules")
        d.resize(520, 420)
        vb = QVBoxLayout(d)
        lst = QListWidget()
        for r in self.rules:
            QListWidgetItem(f"{r.name}  [{r.action}]  OID={r.match_oid}  IP={r.allow_ips}", lst)
        vb.addWidget(lst)
        hb = QHBoxLayout()
        add_b = QPushButton("Add"); edit_b = QPushButton("Edit"); del_b = QPushButton("Delete")
        close_b = QPushButton("Close")
        hb.addWidget(add_b); hb.addWidget(edit_b); hb.addWidget(del_b); hb.addStretch(); hb.addWidget(close_b)
        vb.addLayout(hb)
        def refresh():
            lst.clear()
            for r in self.rules:
                QListWidgetItem(f"{r.name}  [{r.action}]  OID={r.match_oid}  IP={r.allow_ips}", lst)
        def add():
            dd = TrapRuleDialog(TrapRule(), d)
            if dd.exec():
                self.rules.append(dd.rule)
                self._save_rules(); refresh()
        def edit():
            i = lst.currentRow()
            if i < 0: return
            dd = TrapRuleDialog(self.rules[i], d)
            if dd.exec():
                self.rules[i] = dd.rule
                self._save_rules(); refresh()
        def rem():
            i = lst.currentRow()
            if i < 0: return
            del self.rules[i]; self._save_rules(); refresh()
        add_b.clicked.connect(add); edit_b.clicked.connect(edit)
        del_b.clicked.connect(rem); close_b.clicked.connect(d.accept)
        d.exec()

    def closeEvent(self, event) -> None:
        self._stop()
        super().closeEvent(event)
