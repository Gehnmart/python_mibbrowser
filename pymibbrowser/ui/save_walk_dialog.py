"""Save Walk — dump a subtree of the live agent into a text file that
Agent Simulator (and net-snmp's snmpwalk output format) can consume.

Output line format:
    .1.3.6.1.2.1.1.1.0 = STRING: Linux lab 6.8.0
    .1.3.6.1.2.1.1.3.0 = Timeticks: (12345) 0:02:03.45
    .1.3.6.1.2.1.2.2.1.2.1 = STRING: lo
    ...

Matches what `snmpwalk -Oq` / iReasoning's "Save to walk file" produce
so the resulting file drops straight into a mock-agent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
)

from ..config import Agent
from ..i18n import _t
from .. import snmp_ops


# Mapping from VarBind.type_name → snmpwalk "<TYPE>" token. Anything not
# explicitly named falls back to STRING (matches snmpwalk default).
_TYPE_TOKENS = {
    "Integer":        "INTEGER",
    "Integer32":      "INTEGER",
    "Counter32":      "Counter32",
    "Counter64":      "Counter64",
    "Gauge32":        "Gauge32",
    "Unsigned32":     "Gauge32",
    "TimeTicks":      "Timeticks",
    "IpAddress":      "IpAddress",
    "ObjectIdentifier": "OID",
    "ObjectIdentity": "OID",
    "OctetString":    "STRING",
    "DisplayString":  "STRING",
    "Opaque":         "Opaque",
    "Null":           "",
    "NoSuchObject":   "",
    "NoSuchInstance": "",
    "EndOfMibView":   "",
}


def _escape_quoted(s: str) -> str:
    # Match what compare_dialog._unescape_quoted reverses: only \ and "
    # get escaped. Newlines are rare enough in SNMP-visible strings that
    # we leave them literal (snmpwalk's own output does the same).
    return s.replace('\\', '\\\\').replace('"', '\\"')


def _format_line(vb) -> Optional[str]:
    oid = "." + ".".join(str(x) for x in vb.oid)
    tok = _TYPE_TOKENS.get(vb.type_name, "STRING")
    # Terminator marker vbs (no value) — skip from output; they're not
    # real rows.
    if vb.type_name in ("NoSuchObject", "NoSuchInstance", "EndOfMibView"):
        return None
    val = vb.display_value
    if tok == "STRING":
        # Quoted + escaped so values containing quotes round-trip
        # correctly through parse_walk_file.
        return f'{oid} = {tok}: "{_escape_quoted(val or "")}"'
    if tok == "":
        return f"{oid} = {val}"
    return f"{oid} = {tok}: {val}"


class _WalkWorker(QObject):
    progress = pyqtSignal(int)              # row count so far
    done = pyqtSignal(list)                 # [(oid_tuple, line_text), ...]
    failed = pyqtSignal(str)

    def __init__(self, agent: Agent, root_oid: tuple[int, ...]) -> None:
        super().__init__()
        self._agent = agent
        self._root = root_oid
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        lines: list[tuple[tuple[int, ...], str]] = []
        try:
            def cb(vb):
                if self._cancel:
                    raise snmp_ops.SnmpError("cancelled")
                ln = _format_line(vb)
                if ln is not None:
                    lines.append((tuple(vb.oid), ln))
                    self.progress.emit(len(lines))
            snmp_ops.op_walk(self._agent, self._root, cb=cb)
            self.done.emit(lines)
        except Exception as exc:
            self.failed.emit(str(exc))


class SaveWalkDialog(QDialog):
    def __init__(self, agent: Agent, tree=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Save walk to file"))
        self.resize(720, 520)
        self.agent = agent
        self.tree = tree
        self._thread: Optional[QThread] = None
        self._worker: Optional[_WalkWorker] = None
        self._result_lines: list[tuple[tuple[int, ...], str]] = []

        v = QVBoxLayout(self)

        v.addWidget(QLabel(_t(
            "Walk a subtree of {host}:{port} and save the result as a "
            "snmpwalk-compatible text file. Use it offline for diagnosis, "
            "share with colleagues, or load it into Agent Simulator as a "
            "mock device.").format(host=agent.host, port=agent.port)))

        row = QHBoxLayout()
        row.addWidget(QLabel(_t("Starting OID") + ":"))
        self.oid_edit = QLineEdit(".1.3.6.1.2.1")
        self.oid_edit.setToolTip(_t(
            "Starting OID of the walk. '.1.3.6.1.2.1' (mib-2) is a common "
            "choice; '.1' grabs everything the agent publishes."))
        row.addWidget(self.oid_edit, 1)
        self.run_b = QPushButton(_t("Walk ▶"))
        self.run_b.setDefault(True)
        self.run_b.clicked.connect(self._run)
        row.addWidget(self.run_b)
        self.stop_b = QPushButton(_t("Stop"))
        self.stop_b.setEnabled(False)
        self.stop_b.clicked.connect(self._stop)
        row.addWidget(self.stop_b)
        v.addLayout(row)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)   # busy indicator until first progress
        self.bar.setFormat("%v " + _t("rows"))
        self.bar.setVisible(False)
        v.addWidget(self.bar)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        f = QFont("Monospace"); f.setStyleHint(QFont.StyleHint.TypeWriter)
        f.setPointSize(9)
        self.preview.setFont(f)
        self.preview.setPlaceholderText(
            _t("Walk output will appear here. Click 'Save…' to write to disk."))
        v.addWidget(self.preview, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #666;")
        v.addWidget(self.status)

        btns = QDialogButtonBox()
        self.save_b = QPushButton(_t("Save…"))
        self.save_b.setEnabled(False)
        self.save_b.clicked.connect(self._save)
        btns.addButton(self.save_b, QDialogButtonBox.ButtonRole.AcceptRole)
        close_b = QPushButton(_t("Close"))
        close_b.clicked.connect(self.reject)
        btns.addButton(close_b, QDialogButtonBox.ButtonRole.RejectRole)
        v.addWidget(btns)

    def _resolve_oid(self, text: str) -> Optional[tuple[int, ...]]:
        text = text.strip()
        if not text:
            return None
        if self.tree is not None:
            t = self.tree.resolve_name(text)
            if t is not None:
                return t
        try:
            return tuple(int(p) for p in text.strip(".").split("."))
        except ValueError:
            return None

    def _run(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        oid = self._resolve_oid(self.oid_edit.text())
        if oid is None:
            QMessageBox.warning(self, _t("Save walk to file"),
                                 _t("Cannot resolve OID"))
            return
        self._result_lines = []
        self.preview.clear()
        self.save_b.setEnabled(False)
        self.run_b.setEnabled(False)
        self.stop_b.setEnabled(True)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.status.setText(_t("Walking…"))

        self._thread = QThread(self)
        self._worker = _WalkWorker(self.agent, oid)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.done.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _on_progress(self, n: int) -> None:
        # Flip from busy to determinate once we have numbers rolling in —
        # looks like progress even though we don't know the total.
        self.bar.setRange(0, 0)
        self.bar.setValue(n)
        if n % 50 == 0:
            self.status.setText(_t("Walked {n} rows…").format(n=n))

    def _on_done(self, lines) -> None:
        self.run_b.setEnabled(True)
        self.stop_b.setEnabled(False)
        self.bar.setVisible(False)
        # Sort lexicographically by OID — snmpwalk output is already
        # ordered by GETNEXT semantics but sorting defensively keeps the
        # file predictable for diff'ing.
        lines.sort(key=lambda kv: kv[0])
        self._result_lines = lines
        text = "\n".join(ln for _oid, ln in lines)
        self.preview.setPlainText(text)
        self.save_b.setEnabled(bool(lines))
        self.status.setText(
            _t("Walk finished — {n} rows").format(n=len(lines)))

    def _on_failed(self, msg: str) -> None:
        self.run_b.setEnabled(True)
        self.stop_b.setEnabled(False)
        self.bar.setVisible(False)
        self.status.setText(_t("Failed") + ": " + msg)

    def _save(self) -> None:
        if not self._result_lines:
            return
        default_name = f"{self.agent.host.replace(':', '_')}.walk"
        path, _sel = QFileDialog.getSaveFileName(
            self, _t("Save walk to file"), default_name,
            "Walk (*.walk *.snmpwalk *.txt);;All files (*)")
        if not path:
            return
        try:
            Path(path).write_text(
                "\n".join(ln for _oid, ln in self._result_lines) + "\n",
                encoding="utf-8")
            self.status.setText(_t("Saved to") + f" {path}")
        except Exception as exc:
            QMessageBox.warning(self, _t("Save walk to file"), str(exc))

    def closeEvent(self, ev) -> None:
        self._stop()
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(1000)
        super().closeEvent(ev)
