"""Script Runner dialog — run iReasoning-style SNMP command scripts.

This is the discoverable front end for script_runner.run(). Provides:
  • a monospace editor with line numbers
  • a Reference tab explaining every command + examples
  • Load example buttons for common patterns (periodic GET, alarm on
    threshold, bulk SET)
  • Run in a background thread so the UI stays responsive even on
    scripts that sleep() for minutes
  • live output in a read-only log below the editor
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

from ..config import Agent
from ..i18n import _t
from .. import script_runner


# ---------------------------------------------------------------------------
# Reference text (HTML) and canned examples — presented in the Reference tab
# ---------------------------------------------------------------------------

REFERENCE_HTML = """
<h3>Script commands</h3>
<p>One command per line. Lines starting with <code>#</code> are comments.</p>

<table cellpadding="4" style="border-collapse:collapse">
<tr><th align="left">Command</th><th align="left">Meaning</th></tr>
<tr><td><code>get &lt;host[:port]&gt; &lt;oid&gt; [oid…]</code></td>
    <td>SNMP GET on the target agent. <code>host</code> may be
    <code>192.168.1.1</code> or <code>lab:11161</code>.</td></tr>
<tr><td><code>getnext &lt;host[:port]&gt; &lt;oid&gt; [oid…]</code></td>
    <td>SNMP GET-NEXT (useful as a cheap "is it alive?" probe).</td></tr>
<tr><td><code>set &lt;host[:port]&gt; &lt;oid&gt; &lt;type&gt; &lt;value&gt; […]</code></td>
    <td>SNMP SET. Type is one of: <b>i</b> Integer, <b>u</b> Unsigned,
    <b>t</b> TimeTicks, <b>a</b> IpAddress, <b>o</b> OID, <b>s</b>
    OctetString (text), <b>c</b> Counter, <b>g</b> Gauge, <b>x</b> hex
    bytes. Multiple oid/type/value triples are allowed.</td></tr>
<tr><td><code>sleep &lt;seconds&gt;</code></td>
    <td>Pause. Floats are ok, e.g. <code>sleep 0.25</code>.</td></tr>
<tr><td><code>save &lt;path&gt;</code></td>
    <td>Redirect result lines into this file. Repeat runs create
    <code>foo.txt.1</code>, <code>foo.txt.2</code>, … so you don't
    clobber the last run.</td></tr>
<tr><td><code>if $ &lt;op&gt; &lt;value&gt; &lt;action&gt; [arg]</code></td>
    <td>Conditional on the <b>last</b> result. <code>op</code> is one of
    <code>&gt;</code>, <code>&lt;</code>, <code>&gt;=</code>,
    <code>&lt;=</code>, <code>=</code>, <code>!=</code>, or
    <code>err</code> (true when the previous command failed).
    <code>action</code> is <code>sound</code> (terminal bell),
    <code>email ADDR</code>, or <code>sleep N</code>.</td></tr>
</table>

<h3>OID forms</h3>
<p>Both symbolic and dotted numeric work, resolved through the loaded
MIB tree: <code>sysUpTime.0</code>, <code>.1.3.6.1.2.1.1.3.0</code>,
<code>ifInOctets.4</code>.</p>

<h3>Variables (not supported yet)</h3>
<p>This runner implements iReasoning's core commands — no variable
substitution or loops. Use a wrapper shell script if you need loops
over hosts.</p>
"""


EXAMPLE_PROBE = """\
# Poll sysUpTime once a minute for 5 minutes, log to a file.
save /tmp/uptime.log

get 127.0.0.1:161 sysUpTime.0
sleep 60
get 127.0.0.1:161 sysUpTime.0
sleep 60
get 127.0.0.1:161 sysUpTime.0
sleep 60
get 127.0.0.1:161 sysUpTime.0
sleep 60
get 127.0.0.1:161 sysUpTime.0
"""

EXAMPLE_ALARM = """\
# Ring the terminal bell if sysUpTime drops below 60s — meaning the
# device just rebooted. sysUpTime is in hundredths of a second, so
# 6000 ≈ 60 seconds.
get 127.0.0.1:161 sysUpTime.0
if $ < 6000 sound
"""

EXAMPLE_SET = """\
# Set two scalars on a device. 's' = OctetString (text), 'i' = Integer.
set 127.0.0.1:161 sysContact.0 s "admin@example.com" sysLocation.0 s "rack-A"
"""


# ---------------------------------------------------------------------------
# Worker — runs script_runner.run in its own thread so the UI stays
# responsive while the script sleeps.
# ---------------------------------------------------------------------------

class _ScriptWorker(QObject):
    line = pyqtSignal(str)
    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, path: str, agent: Agent, tree) -> None:
        super().__init__()
        self._path = path
        self._agent = agent
        self._tree = tree

    def run(self) -> None:
        try:
            script_runner.run(self._path, self._agent, self._tree,
                              logger=lambda s: self.line.emit(s))
            self.done.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class ScriptDialog(QDialog):
    def __init__(self, agent: Agent, tree, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Run Script"))
        self.resize(900, 680)
        self.agent = agent
        self.tree = tree
        self._thread: Optional[QThread] = None
        self._worker: Optional[_ScriptWorker] = None

        outer = QVBoxLayout(self)

        # Top row: file path + Open / Save / Load example(s)
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(_t("Path to script file (optional)"))
        open_b = QPushButton(_t("Open…"))
        open_b.clicked.connect(self._open_file)
        save_b = QPushButton(_t("Save…"))
        save_b.clicked.connect(self._save_file)
        row.addWidget(QLabel(_t("File") + ":"))
        row.addWidget(self.path_edit, 1)
        row.addWidget(open_b)
        row.addWidget(save_b)
        outer.addLayout(row)

        # Body: tabs for Script + Reference, split with output log
        tabs = QTabWidget()
        self.editor = QPlainTextEdit()
        self.editor.setFont(self._mono_font())
        self.editor.setPlaceholderText(
            _t("Type or paste script here, or click 'Load example'"))
        tabs.addTab(self.editor, _t("Script"))

        ref = QTextBrowser()
        ref.setHtml(REFERENCE_HTML)
        ref.setOpenExternalLinks(False)
        tabs.addTab(ref, _t("Reference"))

        # Example quick-picks — above the output log
        ex_row = QHBoxLayout()
        ex_row.addWidget(QLabel(_t("Load example") + ":"))
        self._add_example_btn(ex_row, _t("Periodic GET"), EXAMPLE_PROBE)
        self._add_example_btn(ex_row, _t("Threshold alarm"), EXAMPLE_ALARM)
        self._add_example_btn(ex_row, _t("Bulk SET"), EXAMPLE_SET)
        ex_row.addStretch()

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(self._mono_font())
        self.output.setMaximumBlockCount(5000)

        body = QSplitter(Qt.Orientation.Vertical)
        body.addWidget(tabs)
        # Wrap the output + example row in a container so they stay
        # together in the splitter.
        out_container = QWidget()
        ov = QVBoxLayout(out_container)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.addLayout(ex_row)
        ov.addWidget(self.output, 1)
        body.addWidget(out_container)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 2)
        outer.addWidget(body, 1)

        # Bottom buttons
        btns = QDialogButtonBox()
        self.run_b = QPushButton(_t("Run ▶"))
        self.run_b.setDefault(True)
        self.run_b.clicked.connect(self._run)
        btns.addButton(self.run_b, QDialogButtonBox.ButtonRole.AcceptRole)
        clear_b = QPushButton(_t("Clear output"))
        clear_b.clicked.connect(lambda: self.output.clear())
        btns.addButton(clear_b, QDialogButtonBox.ButtonRole.ResetRole)
        close_b = QPushButton(_t("Close"))
        close_b.clicked.connect(self.reject)
        btns.addButton(close_b, QDialogButtonBox.ButtonRole.RejectRole)
        outer.addWidget(btns)

        # Ctrl+Enter = Run — matches the toolbar's Go ▶ idiom.
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._run)

    # --- helpers ---------------------------------------------------------

    def _mono_font(self) -> QFont:
        f = QFont("Monospace")
        f.setStyleHint(QFont.StyleHint.TypeWriter)
        f.setPointSize(10)
        return f

    def _add_example_btn(self, layout, label: str, text: str) -> None:
        b = QPushButton(label)
        b.clicked.connect(lambda: self._load_example(text))
        layout.addWidget(b)

    def _load_example(self, text: str) -> None:
        if self.editor.toPlainText().strip():
            btn = QMessageBox.question(
                self, _t("Load example"),
                _t("Replace the current script with this example?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if btn != QMessageBox.StandardButton.Yes:
                return
        self.editor.setPlainText(text)
        self.path_edit.setText("")

    # --- file I/O --------------------------------------------------------

    def _open_file(self) -> None:
        path, _sel = QFileDialog.getOpenFileName(
            self, _t("Open Script"), "",
            "Script (*.txt *.snmp *.script);;All files (*)")
        if not path:
            return
        try:
            self.editor.setPlainText(Path(path).read_text())
            self.path_edit.setText(path)
        except Exception as exc:
            QMessageBox.warning(self, _t("Open Script"), str(exc))

    def _save_file(self) -> None:
        path = self.path_edit.text().strip()
        if not path:
            path, _sel = QFileDialog.getSaveFileName(
                self, _t("Save Script"), "script.txt",
                "Script (*.txt *.snmp);;All files (*)")
            if not path:
                return
            self.path_edit.setText(path)
        try:
            Path(path).write_text(self.editor.toPlainText())
            self._append_output(f"saved to {path}\n")
        except Exception as exc:
            QMessageBox.warning(self, _t("Save Script"), str(exc))

    # --- run -------------------------------------------------------------

    def _run(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            QMessageBox.information(self, _t("Run Script"),
                                     _t("A script is already running."))
            return
        text = self.editor.toPlainText()
        if not text.strip():
            QMessageBox.information(self, _t("Run Script"),
                                     _t("Script is empty."))
            return
        # Unique per-run temp file — previous "last_script.txt" race
        # corrupted the script if the user hit Ctrl+Enter twice before
        # the first run finished writing.
        import tempfile
        try:
            fd = tempfile.NamedTemporaryFile(
                "w", suffix=".script", prefix="pymib_", delete=False,
                encoding="utf-8")
            fd.write(text)
            fd.close()
            tmp_path = fd.name
        except Exception as exc:
            QMessageBox.warning(self, _t("Run Script"), str(exc))
            return
        self._current_tmp = tmp_path

        self.output.appendPlainText(
            "=== " + _t("Running script") + f" ({self.agent.host}:{self.agent.port}) ===")

        self.run_b.setEnabled(False)
        self._thread = QThread(self)
        self._worker = _ScriptWorker(tmp_path, self.agent, self.tree)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.line.connect(self._append_output)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.done.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup_tmp)
        self._thread.start()

    def _cleanup_tmp(self) -> None:
        """Remove the per-run temp script file. Called on thread
        finish — by then the runner has closed its own handle."""
        path = getattr(self, "_current_tmp", None)
        if not path:
            return
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass
        self._current_tmp = None

    def _append_output(self, s: str) -> None:
        self.output.appendPlainText(s.rstrip())

    def _on_done(self) -> None:
        self.run_b.setEnabled(True)
        self.output.appendPlainText("=== " + _t("Script finished") + " ===")

    def _on_failed(self, msg: str) -> None:
        self.run_b.setEnabled(True)
        self.output.appendPlainText("!!! " + _t("Script failed") + ": " + msg)
