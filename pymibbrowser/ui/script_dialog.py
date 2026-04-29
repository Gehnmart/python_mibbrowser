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

from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import workers
from ..infra import script_runner
from ..infra.config import Agent
from ..infra.i18n import _t

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
<tr><td><code>if &lt;lhs&gt; &lt;op&gt; &lt;value&gt; &lt;action&gt; [arg]</code></td>
    <td>One-line conditional. <code>lhs</code> is <code>$</code> (last
    result) or any <code>$NAME</code> variable. <code>op</code> is
    <code>&gt;</code>, <code>&lt;</code>, <code>&gt;=</code>,
    <code>&lt;=</code>, <code>=</code>, <code>!=</code>, or
    <code>err</code> (true when the previous command failed).
    <code>action</code> is <code>sound</code>, <code>print MSG</code>,
    <code>notify MSG</code>, <code>email ADDR</code>,
    <code>sleep N</code>, or <code>abort</code>. Block form below for
    multi-action branches.</td></tr>
<tr><td><code>let &lt;name&gt; [=] &lt;value&gt;</code></td>
    <td>Bind a variable for use in later commands. Names match
    <code>[A-Za-z_][A-Za-z0-9_]*</code>; the <code>=</code> is
    optional. The right-hand side is itself substituted, so
    <code>let prev $last</code> snapshots the most recent result.</td></tr>
<tr><td><code>print &lt;message&gt;</code></td>
    <td>Emit a line through the log pane. <code>$</code> substitution
    applies, surrounding quotes are stripped:
    <code>print "uptime is $last"</code>.</td></tr>
<tr><td><code>notify &lt;message&gt;</code></td>
    <td>Desktop notification (libnotify on Linux, osascript on macOS).
    Falls back to a <code>[notify]</code>-tagged log line if no
    backend is available.</td></tr>
<tr><td><code>abort</code></td>
    <td>Stop the script immediately. Subsequent commands — including
    those inside enclosing <code>if</code>-blocks — are skipped.</td></tr>
</table>

<h3>Block conditionals</h3>
<p>Drop the action token to make <code>if</code> a multi-line block.
Close with <code>end</code>; <code>else</code> in between is optional.
Blocks nest, and any command (including another <code>if</code>) can
appear inside.</p>
<pre>if $now &gt; $prev
    print "rate up: $prev → $now"
    notify "Counter increased"
else
    print "stable: $now"
end</pre>
<p>One-liners stay supported as a shorthand for a single-action body:
<code>if $ &gt; 50 sound</code>, <code>if $ err abort</code>.</p>

<h3>OID forms</h3>
<p>Both symbolic and dotted numeric work, resolved through the loaded
MIB tree: <code>sysUpTime.0</code>, <code>.1.3.6.1.2.1.1.3.0</code>,
<code>ifInOctets.4</code>.</p>

<h3>Variables</h3>
<p>Reference any bound variable as <code>$NAME</code> in a host, OID,
SET value, save target, or <code>if</code> operand/arg. A variable
that holds <code>host:port</code> still routes correctly when used as
the host token (the runner re-splits after substitution). Two
built-ins are always available without an explicit
<code>let</code>:</p>
<ul>
  <li><code>$last</code> — display value of the most recent SNMP
  result (empty before the first command runs);</li>
  <li><code>$err</code> — last error flag, <code>"0"</code> on
  success, <code>"1"</code> after a failed command.</li>
</ul>
<p>Unknown <code>$NAME</code> tokens are left as the literal text so
the failure surfaces as an "unresolved OID" or socket error rather
than a silently-empty token.</p>
<pre>let target = 127.0.0.1:11161
let oid    = sysUpTime.0
get $target $oid
let prev $last
sleep 5
get $target $oid
if $ &gt; $prev sound</pre>
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

EXAMPLE_BLOCK = """\
# Block-form if / else / end with print + notify + abort.
# Sample sysUpTime twice; react to up / steady / agent-down.

let target = 127.0.0.1:161
let oid    = sysUpTime.0

get $target $oid
if $ err
    print "agent unreachable on first probe — bailing out"
    abort
end
let prev $last

sleep 5

get $target $oid
if $ err
    notify "agent went away mid-run"
    abort
end
let now $last

if $now > $prev
    print "uptime advancing: $prev → $now"
    notify "agent is alive ($now ticks)"
else
    print "uptime did not advance — counter wrap or restart?"
    notify "sysUpTime stalled at $now"
end
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
        self._cancel = False

    def cancel(self) -> None:
        """Break the script runner out of its loop (and out of any
        'sleep N' it's currently in) at the next 100-ms check."""
        self._cancel = True

    def run(self) -> None:
        try:
            script_runner.run(
                self._path, self._agent, self._tree,
                logger=lambda s: self.line.emit(s),
                should_cancel=lambda: self._cancel,
            )
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
        self._thread: QThread | None = None
        self._worker: _ScriptWorker | None = None

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
        self._add_example_btn(ex_row, _t("Block if/else"), EXAMPLE_BLOCK)
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
        if workers.is_thread_alive(self._thread):
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

    def closeEvent(self, ev) -> None:
        """Cancel an in-flight script so closing the dialog doesn't
        leave a QThread blocked in `sleep 3600`."""
        from .. import workers
        if self._worker is not None:
            try:
                self._worker.cancel()
            except Exception:
                pass
        if self._thread is not None:
            workers.wait_if_running(self._thread, 500)
        super().closeEvent(ev)
