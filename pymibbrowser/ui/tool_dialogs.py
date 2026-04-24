"""
Tools dialogs:
  • TrapSenderDialog — compose and send SNMP v1/v2c traps.
  • AgentSimulatorDialog — run the built-in simulator on a port.
  • MibEditorDialog — simple text editor for MIB files with parse check.
  • TrapdConsoleDialog — headless trap receiver with tail log.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import config, trap_sender
from ..config import Agent
from ..i18n import _t
from ..mib_loader import MibTree
from ..simulator import SnmpAgentSim
from ..trap_receiver import TrapEvent, TrapListener

# ---------------------------------------------------------------------------
# Trap sender
# ---------------------------------------------------------------------------

class TrapSenderDialog(QDialog):
    def __init__(self, tree: MibTree, agent: Agent, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Trap Sender"))
        self.tree = tree
        self.agent = agent
        self.resize(620, 420)

        form = QFormLayout()
        self.host_e = QLineEdit(agent.host)
        self.port_e = QSpinBox(); self.port_e.setRange(1, 65535); self.port_e.setValue(162)
        self.comm_e = QLineEdit(agent.read_community)
        self.ver_c  = QComboBox(); self.ver_c.addItems(("1", "2c")); self.ver_c.setCurrentText("2c")
        self.trap_oid_e = QLineEdit(".1.3.6.1.6.3.1.1.5.1")   # coldStart default
        form.addRow(_t("Receiver host"), self.host_e)
        form.addRow(_t("Port"), self.port_e)
        form.addRow(_t("Community"), self.comm_e)
        form.addRow(_t("Version"), self.ver_c)
        form.addRow(_t("Trap OID"), self.trap_oid_e)

        self.vbs_table = QTableWidget(0, 3)
        self.vbs_table.setHorizontalHeaderLabels(("OID/name", "Type", "Value"))
        self.vbs_table.horizontalHeader().setStretchLastSection(True)

        add_btn = QPushButton(_t("+ Add var-bind")); add_btn.clicked.connect(self._add_row)
        del_btn = QPushButton(_t("− Remove")); del_btn.clicked.connect(self._del_row)
        hb = QHBoxLayout(); hb.addWidget(add_btn); hb.addWidget(del_btn); hb.addStretch()

        send_btn = QPushButton(_t("Send trap")); send_btn.clicked.connect(self._send)
        close_btn = QPushButton(_t("Close")); close_btn.clicked.connect(self.accept)
        footer = QHBoxLayout()
        self.status = QLabel(_t(" "))
        footer.addWidget(self.status, 1); footer.addWidget(send_btn); footer.addWidget(close_btn)

        v = QVBoxLayout(self)
        v.addLayout(form)
        v.addWidget(QLabel(_t("Variable bindings:")))
        v.addWidget(self.vbs_table, 1)
        v.addLayout(hb)
        v.addLayout(footer)

    def _add_row(self) -> None:
        r = self.vbs_table.rowCount()
        self.vbs_table.insertRow(r)
        self.vbs_table.setItem(r, 0, QTableWidgetItem(""))
        t = QComboBox(); t.addItems(("i","u","t","a","o","s","c","g","x"))
        self.vbs_table.setCellWidget(r, 1, t)
        self.vbs_table.setItem(r, 2, QTableWidgetItem(""))

    def _del_row(self) -> None:
        r = self.vbs_table.currentRow()
        if r >= 0:
            self.vbs_table.removeRow(r)

    def _send(self) -> None:
        from .. import snmp_ops
        trap_oid = self.trap_oid_e.text().strip().lstrip(".")
        if not trap_oid:
            QMessageBox.warning(self, "Trap", "Trap OID required.")
            return
        pairs = []
        for r in range(self.vbs_table.rowCount()):
            oid_item = self.vbs_table.item(r, 0)
            val_item = self.vbs_table.item(r, 2)
            type_w = self.vbs_table.cellWidget(r, 1)
            if not oid_item or not val_item or not type_w:
                continue
            oid = oid_item.text().strip()
            if not oid:
                continue
            resolved = self.tree.resolve_name(oid)
            if resolved is None:
                QMessageBox.warning(self, "Trap", f"Unresolved OID: {oid}")
                return
            try:
                val = snmp_ops.build_set_value(type_w.currentText(), val_item.text())
            except Exception as exc:
                QMessageBox.warning(self, "Trap", f"Bad value at row {r}: {exc}")
                return
            pairs.append(("." + ".".join(str(p) for p in resolved), val))

        try:
            result = trap_sender.send_trap(
                self.host_e.text().strip(), int(self.port_e.value()),
                self.comm_e.text(), self.ver_c.currentText(),
                "." + trap_oid, pairs,
            )
            self.status.setText(f"→ {result}")
        except Exception as exc:
            self.status.setText(f"error: {exc}")
            QMessageBox.warning(self, "Trap", str(exc))


# ---------------------------------------------------------------------------
# Agent simulator
# ---------------------------------------------------------------------------

class AgentSimulatorDialog(QDialog):
    def __init__(self, tree: MibTree, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("SNMP Agent Simulator"))
        self.resize(600, 420)
        self.sim = SnmpAgentSim()

        v = QVBoxLayout(self)
        form = QFormLayout()
        self.bind_e = QLineEdit("127.0.0.1")
        self.bind_e.setToolTip(_t("0.0.0.0 to listen on all interfaces"))
        self.port_e = QSpinBox(); self.port_e.setRange(1, 65535); self.port_e.setValue(1161)
        self.comm_e = QLineEdit("public")
        self.walk_e = QLineEdit(); self.walk_e.setPlaceholderText(
            "path to snmpwalk file (optional)")
        browse = QPushButton(_t("Browse…")); browse.clicked.connect(self._browse)
        wh = QHBoxLayout(); wh.addWidget(self.walk_e, 1); wh.addWidget(browse)
        form.addRow(_t("Bind host"), self.bind_e)
        form.addRow(_t("Port"), self.port_e)
        form.addRow(_t("Community"), self.comm_e)
        form.addRow(_t("Walk file"), wh)
        v.addLayout(form)

        hb = QHBoxLayout()
        self.start_btn = QPushButton(_t("Start")); self.start_btn.clicked.connect(self._start)
        self.stop_btn  = QPushButton(_t("Stop"));  self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        self.status = QLabel(_t("stopped"))
        hb.addWidget(self.start_btn); hb.addWidget(self.stop_btn); hb.addWidget(self.status, 1)
        v.addLayout(hb)

        v.addWidget(QLabel("Tip: run `./run.sh` → Operations toolbar → Address set to "
                           "127.0.0.1:<port> → Walk .1 to dump the simulated agent."))
        v.addStretch()

    def _browse(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Walk file", "",
                                            "Text (*.txt *.walk *.snmpwalk);;All (*)")
        if p:
            self.walk_e.setText(p)

    def _start(self) -> None:
        self.sim.port = int(self.port_e.value())
        self.sim.community = self.comm_e.text()
        walk = self.walk_e.text().strip()
        if walk:
            try:
                n = self.sim.load_from_walk(walk)
                self.status.setText(f"loaded {n} OIDs")
            except Exception as exc:
                QMessageBox.warning(self, "Simulator", f"Load failed: {exc}")
                return
        bind = self.bind_e.text().strip() or "127.0.0.1"
        try:
            self.sim.start(bind)
        except OSError as exc:
            QMessageBox.warning(self, "Simulator", f"Cannot bind: {exc}")
            return
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status.setText(f"listening {bind}:{self.sim.port} "
                            f"({len(self.sim._data)} OIDs)")

    def _stop(self) -> None:
        self.sim.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status.setText("stopped")

    def closeEvent(self, event) -> None:
        self._stop(); super().closeEvent(event)


# ---------------------------------------------------------------------------
# MIB editor
# ---------------------------------------------------------------------------

class MibEditorDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("MIB Editor"))
        self.resize(820, 560)
        self._current_path: Path | None = None

        v = QVBoxLayout(self)
        hb = QHBoxLayout()
        open_b = QPushButton(_t("Open…")); open_b.clicked.connect(self._open)
        save_b = QPushButton(_t("Save")); save_b.clicked.connect(self._save)
        saveas_b = QPushButton(_t("Save as…")); saveas_b.clicked.connect(self._save_as)
        check_b = QPushButton(_t("Parse check")); check_b.clicked.connect(self._check)
        hb.addWidget(open_b); hb.addWidget(save_b); hb.addWidget(saveas_b); hb.addStretch()
        hb.addWidget(check_b)
        v.addLayout(hb)

        split = QSplitter(Qt.Orientation.Vertical)
        self.edit = QPlainTextEdit()
        self.edit.setFont(QFont("monospace"))
        split.addWidget(self.edit)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500)
        split.addWidget(self.log)
        split.setSizes([420, 140])
        v.addWidget(split, 1)

    def _open(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Open MIB", "",
                                            "MIB (*.mib *.my *.txt *.smi);;All (*)")
        if not p:
            return
        try:
            self.edit.setPlainText(Path(p).read_text(encoding="utf-8", errors="replace"))
            self._current_path = Path(p)
            self.setWindowTitle(f"MIB Editor — {p}")
        except Exception as exc:
            QMessageBox.warning(self, "MIB Editor", str(exc))

    def _save(self) -> None:
        if self._current_path is None:
            return self._save_as()
        self._current_path.write_text(self.edit.toPlainText(), encoding="utf-8")
        self.log.appendPlainText(f"saved → {self._current_path}")

    def _save_as(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Save MIB", "new.mib",
                                            "MIB (*.mib *.my *.txt);;All (*)")
        if not p:
            return
        self._current_path = Path(p)
        self._save()

    def _check(self) -> None:
        # Run pysmi on this buffer (to tmp file) and show diagnostics.
        import tempfile

        from pysmi.codegen import JsonCodeGen
        from pysmi.compiler import MibCompiler
        from pysmi.parser import SmiV1CompatParser
        from pysmi.reader import FileReader
        from pysmi.searcher import AnyFileSearcher, StubSearcher
        from pysmi.writer import FileWriter

        from ..mib_loader import STUB_MIBS

        with tempfile.TemporaryDirectory() as tmpd:
            name = self._current_path.stem if self._current_path else "SCRATCH-MIB"
            src = Path(tmpd) / name
            src.write_text(self.edit.toPlainText(), encoding="utf-8")
            dest = Path(tmpd) / "out"
            dest.mkdir()
            compiler = MibCompiler(SmiV1CompatParser(tempdir=""),
                                   JsonCodeGen(),
                                   FileWriter(str(dest)).set_options(suffix=".json"))
            compiler.add_sources(FileReader(tmpd),
                                 FileReader(str(config.default_mibs_src())))
            compiler.add_searchers(StubSearcher(*STUB_MIBS),
                                   AnyFileSearcher(str(dest)).set_options(exts=[".json"]))
            self.log.clear()
            try:
                results = compiler.compile(name, rebuild=True, genTexts=False,
                                           ignoreErrors=True)
                ok = any(v == "compiled" for v in results.values())
                self.log.appendPlainText("\n".join(
                    f"{k}: {v}" for k, v in results.items()))
                if ok:
                    self.log.appendPlainText("OK")
                else:
                    self.log.appendPlainText("No modules compiled successfully.")
            except Exception as exc:
                self.log.appendPlainText(f"error: {exc}")


# ---------------------------------------------------------------------------
# Trapd console (headless trap tail)
# ---------------------------------------------------------------------------

class _LogBridge(QObject):
    line = pyqtSignal(str)


class TrapdConsoleDialog(QDialog):
    def __init__(self, tree: MibTree, settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Trap daemon log"))
        self.resize(780, 420)
        self.tree = tree
        self.settings = settings
        self.bridge = _LogBridge()
        self.listener: TrapListener | None = None

        v = QVBoxLayout(self)
        hb = QHBoxLayout()
        hb.addWidget(QLabel(_t("Port:")))
        self.port_e = QSpinBox(); self.port_e.setRange(1, 65535); self.port_e.setValue(settings.trap_port)
        hb.addWidget(self.port_e)
        self.start_b = QPushButton(_t("Start")); self.start_b.clicked.connect(self._start); hb.addWidget(self.start_b)
        self.stop_b  = QPushButton(_t("Stop"));  self.stop_b.clicked.connect(self._stop);  hb.addWidget(self.stop_b)
        self.stop_b.setEnabled(False)
        clear_b = QPushButton(_t("Clear")); clear_b.clicked.connect(lambda: self.log.clear()); hb.addWidget(clear_b)
        hb.addStretch()
        v.addLayout(hb)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setFont(QFont("monospace")); self.log.setMaximumBlockCount(5000)
        v.addWidget(self.log, 1)

        self.bridge.line.connect(self.log.appendPlainText)

    def _on_trap(self, ev: TrapEvent) -> None:
        import time as _t
        vbs = "; ".join(f"{o}={v}" for o, _, v in ev.var_binds)
        line = (f"{_t.strftime('%H:%M:%S')}  {ev.source_ip}:{ev.source_port}  "
                f"v{ev.version}  {ev.trap_oid}  [{vbs}]")
        self.bridge.line.emit(line)

    def _start(self) -> None:
        self.listener = TrapListener(int(self.port_e.value()), on_trap=self._on_trap)
        try:
            self.listener.start()
        except PermissionError as exc:
            QMessageBox.critical(self, "Trapd", str(exc)); self.listener = None; return
        self.start_b.setEnabled(False); self.stop_b.setEnabled(True); self.port_e.setEnabled(False)
        self.bridge.line.emit(f"# listening on 0.0.0.0:{self.port_e.value()}")

    def _stop(self) -> None:
        if self.listener:
            self.listener.stop()
            self.listener = None
        self.start_b.setEnabled(True); self.stop_b.setEnabled(False); self.port_e.setEnabled(True)
        self.bridge.line.emit("# stopped")

    def closeEvent(self, event) -> None:
        self._stop(); super().closeEvent(event)
