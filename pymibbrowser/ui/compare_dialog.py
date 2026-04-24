"""Compare devices / walks.

Pick two sources (each = live agent OR a saved walk file) and render a
three-column diff:

  • equal rows      — grey (or hidden via checkbox)
  • only on left    — blue background
  • only on right   — orange background
  • value differs   — red background

Goes straight to the common troubleshooting question "what's different
between these two devices?" / "what changed on THIS device since last
snapshot?" without exporting to text and running diff manually.
"""
from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import snmp_ops
from ..config import Agent, AppSettings
from ..i18n import _t

# ---------------------------------------------------------------------------
# Walk-file parsing (snmpwalk -Oq / our SaveWalkDialog output format)
# ---------------------------------------------------------------------------

# Line forms we need to parse:
#   .1.3.6.1.2.1.1.1.0 = STRING: "Linux lab"
#   .1.3.6.1.2.1.1.1.0 = STRING: "Linux a\"b\"c"     ← escaped quotes
#   .1.3.6.1.2.1.1.3.0 = Timeticks: (12345) 0:02:03.45
#   .1.3.6.1.2.1.2.2.1.2.1 = STRING: lo              ← unquoted variant
#   .1.3.6.1.2.1.2.2.1.8.1 = INTEGER: up(1)
#
# Quoted strings may contain \" and \\ escape sequences (snmpwalk's -Oa
# output uses them when the value has embedded quotes). We match the
# whole quoted run including escapes, then unescape on store.
_OID_PART = r'\.?((?:\d+\.)*\d+)'
_TYPE_PART = r'(?:[A-Za-z0-9\-]+:\s*)?'
_QUOTED = r'"(?:[^"\\]|\\.)*"'
_WALK_LINE = re.compile(
    rf'^{_OID_PART}\s*=\s*{_TYPE_PART}({_QUOTED}|.*)$')


def _unescape_quoted(s: str) -> str:
    # s arrives with the surrounding quotes; strip them, then replace the
    # two snmpwalk-relevant escapes. Don't use json.loads — it's stricter
    # (rejects \x, \'). This matches what the Save walk writer produced.
    if len(s) < 2 or not (s.startswith('"') and s.endswith('"')):
        return s
    inner = s[1:-1]
    return inner.replace('\\"', '"').replace('\\\\', '\\')


def parse_walk_file(path: str) -> dict[tuple[int, ...], str]:
    """Parse an snmpwalk-compatible .walk file.

    >>> import tempfile, os
    >>> tmp = tempfile.NamedTemporaryFile('w', suffix='.walk', delete=False)
    >>> _ = tmp.write('.1.3.6.1.2.1.1.1.0 = STRING: "Linux a\\\\"b\\\\" c"\\n')
    >>> _ = tmp.write('.1.3.6.1.2.1.1.3.0 = Timeticks: (1) 0:00:00.01\\n')
    >>> tmp.close()
    >>> d = parse_walk_file(tmp.name)
    >>> d[(1,3,6,1,2,1,1,1,0)]
    'Linux a"b" c'
    >>> os.unlink(tmp.name)
    """
    out: dict[tuple[int, ...], str] = {}
    for raw in Path(path).read_text(encoding="utf-8",
                                      errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _WALK_LINE.match(line)
        if not m:
            continue
        oid_str, value = m.group(1), m.group(2)
        try:
            oid = tuple(int(p) for p in oid_str.split("."))
        except ValueError:
            continue
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = _unescape_quoted(value)
        out[oid] = value
    return out


# ---------------------------------------------------------------------------
# Walker — collects an entire subtree from a live agent into a dict
# ---------------------------------------------------------------------------

class _WalkCollector(QObject):
    progress = pyqtSignal(int)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, agent: Agent, root_oid: tuple[int, ...]) -> None:
        super().__init__()
        self._agent = agent
        self._root = root_oid
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        out: dict[tuple[int, ...], str] = {}
        try:
            def cb(vb):
                if self._cancel:
                    raise snmp_ops.SnmpError("cancelled")
                if vb.type_name in ("NoSuchObject", "NoSuchInstance",
                                     "EndOfMibView"):
                    return
                out[tuple(vb.oid)] = str(vb.display_value)
                self.progress.emit(len(out))
            snmp_ops.op_walk(self._agent, self._root, cb=cb)
            self.done.emit(out)
        except Exception as exc:
            self.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# Source picker widget — radio "Live agent" vs "Walk file"
# ---------------------------------------------------------------------------

class _SourcePicker(QGroupBox):
    def __init__(self, title: str, settings: AppSettings, parent=None) -> None:
        super().__init__(title, parent)
        self.settings = settings
        v = QVBoxLayout(self)

        row1 = QHBoxLayout()
        self.rb_live = QRadioButton(_t("Live agent"))
        self.rb_live.setChecked(True)
        self.rb_file = QRadioButton(_t("Walk file"))
        row1.addWidget(self.rb_live); row1.addWidget(self.rb_file)
        row1.addStretch()
        v.addLayout(row1)

        row2 = QHBoxLayout()
        self.agent_combo = QComboBox()
        self.agent_combo.setEditable(True)
        self._fill_agents()
        row2.addWidget(self.agent_combo, 1)
        self.file_edit = QLineEdit()
        self.file_edit.setVisible(False)
        self.file_edit.setPlaceholderText(_t("Path to .walk file"))
        row2.addWidget(self.file_edit, 1)
        self.browse_b = QPushButton(_t("Browse…"))
        self.browse_b.setVisible(False)
        self.browse_b.clicked.connect(self._pick_file)
        row2.addWidget(self.browse_b)
        v.addLayout(row2)

        self.rb_live.toggled.connect(self._sync_mode)
        self._sync_mode()

    def _fill_agents(self) -> None:
        for a in [self.settings.current_agent, *self.settings.saved_agents]:
            self.agent_combo.addItem(f"{a.host}:{a.port}")

    def _sync_mode(self) -> None:
        live = self.rb_live.isChecked()
        self.agent_combo.setVisible(live)
        self.file_edit.setVisible(not live)
        self.browse_b.setVisible(not live)

    def _pick_file(self) -> None:
        p, _sel = QFileDialog.getOpenFileName(
            self, _t("Walk file"), "",
            "Walk (*.walk *.snmpwalk *.txt);;All files (*)")
        if p:
            self.file_edit.setText(p)

    def resolve(self) -> tuple[str, object]:
        """Return ('live', Agent) or ('file', path)."""
        if self.rb_live.isChecked():
            spec = self.agent_combo.currentText().strip()
            host, _, port_s = spec.partition(":")
            try:
                port = int(port_s) if port_s else 161
            except ValueError:
                port = 161
            # Start from current_agent so we inherit community/version/…
            ag = Agent(**vars(self.settings.current_agent))
            ag.host = host or "127.0.0.1"
            ag.port = port
            return "live", ag
        return "file", self.file_edit.text().strip()


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class CompareDialog(QDialog):
    def __init__(self, settings: AppSettings, tree=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Compare devices"))
        self.resize(1000, 640)
        self.settings = settings
        self.tree = tree
        self._left: dict[tuple[int, ...], str] = {}
        self._right: dict[tuple[int, ...], str] = {}
        self._pending = 0
        self._thread_l: QThread | None = None
        self._thread_r: QThread | None = None
        self._worker_l: _WalkCollector | None = None
        self._worker_r: _WalkCollector | None = None

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        self.left_picker = _SourcePicker(_t("Left"), settings)
        self.right_picker = _SourcePicker(_t("Right"), settings)
        top.addWidget(self.left_picker, 1)
        top.addWidget(self.right_picker, 1)
        outer.addLayout(top)

        row = QHBoxLayout()
        row.addWidget(QLabel(_t("Subtree OID") + ":"))
        self.oid_edit = QLineEdit(".1.3.6.1.2.1")
        row.addWidget(self.oid_edit, 1)
        self.run_b = QPushButton(_t("Compare ▶"))
        self.run_b.setDefault(True)
        self.run_b.clicked.connect(self._run)
        row.addWidget(self.run_b)
        self.stop_b = QPushButton(_t("Stop"))
        self.stop_b.setEnabled(False)
        self.stop_b.clicked.connect(self._stop)
        row.addWidget(self.stop_b)
        outer.addLayout(row)

        # Filter checkboxes to hide noise.
        filter_row = QHBoxLayout()
        self.hide_equal = QCheckBox(_t("Hide equal rows"))
        self.hide_equal.setChecked(True)
        self.hide_equal.toggled.connect(self._rerender)
        filter_row.addWidget(self.hide_equal)
        filter_row.addStretch()
        outer.addLayout(filter_row)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setVisible(False)
        outer.addWidget(self.bar)

        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(
            [_t("OID"), _t("Left"), _t("Right"), _t("Status")])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        outer.addWidget(self.tbl, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #666;")
        outer.addWidget(self.status)

        btns = QDialogButtonBox()
        export_b = QPushButton(_t("Export CSV"))
        export_b.clicked.connect(self._export_csv)
        btns.addButton(export_b, QDialogButtonBox.ButtonRole.ActionRole)
        close_b = QPushButton(_t("Close"))
        close_b.clicked.connect(self.reject)
        btns.addButton(close_b, QDialogButtonBox.ButtonRole.RejectRole)
        outer.addWidget(btns)

    # --- helpers ------------------------------------------------------

    def _resolve_oid(self, text: str) -> tuple[int, ...] | None:
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

    # --- run ----------------------------------------------------------

    def _run(self) -> None:
        if self._pending > 0:
            return
        oid = self._resolve_oid(self.oid_edit.text())
        if oid is None:
            QMessageBox.warning(self, _t("Compare devices"),
                                 _t("Cannot resolve OID"))
            return
        left_src = self.left_picker.resolve()
        right_src = self.right_picker.resolve()

        self._left = {}
        self._right = {}
        self.tbl.setRowCount(0)
        self.run_b.setEnabled(False)
        self.stop_b.setEnabled(True)
        self.bar.setVisible(True)
        self.status.setText(_t("Walking both sources…"))

        self._pending = 0
        # Left
        if left_src[0] == "live":
            self._pending += 1
            self._thread_l, self._worker_l = self._spawn_live(
                left_src[1], oid, is_left=True)
        else:
            try:
                self._left = parse_walk_file(left_src[1])
            except Exception as exc:
                QMessageBox.warning(self, _t("Compare devices"),
                                     f"left: {exc}")
                self._reset_buttons()
                return
        # Right
        if right_src[0] == "live":
            self._pending += 1
            self._thread_r, self._worker_r = self._spawn_live(
                right_src[1], oid, is_left=False)
        else:
            try:
                self._right = parse_walk_file(right_src[1])
            except Exception as exc:
                QMessageBox.warning(self, _t("Compare devices"),
                                     f"right: {exc}")
                self._reset_buttons()
                return

        if self._pending == 0:
            # Both sides came from files — render immediately.
            self._render()

    def _spawn_live(self, agent: Agent, oid: tuple[int, ...],
                    is_left: bool):
        worker = _WalkCollector(agent, oid)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def on_done(d):
            if is_left:
                self._left = d
            else:
                self._right = d
            self._pending -= 1
            if self._pending == 0:
                self._render()

        def on_failed(msg):
            self._pending -= 1
            side = "left" if is_left else "right"
            self.status.setText(f"{side}: {msg}")
            if self._pending == 0:
                self._render()

        worker.done.connect(on_done)
        worker.failed.connect(on_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        return thread, worker

    def _stop(self) -> None:
        for w in (self._worker_l, self._worker_r):
            if w is not None:
                w.cancel()

    def _reset_buttons(self) -> None:
        self.run_b.setEnabled(True)
        self.stop_b.setEnabled(False)
        self.bar.setVisible(False)

    # --- render -------------------------------------------------------

    def _render(self) -> None:
        self._reset_buttons()
        # Union of OIDs, sorted lexicographically.
        oids = sorted(set(self._left) | set(self._right))
        self._last_rows: list[tuple[tuple[int, ...], str, str, str]] = []
        stats = {"equal": 0, "only_left": 0, "only_right": 0, "diff": 0}
        for oid in oids:
            lv = self._left.get(oid)
            rv = self._right.get(oid)
            if lv is not None and rv is not None:
                if lv == rv:
                    tag = "equal"
                else:
                    tag = "diff"
            elif lv is not None:
                tag = "only_left"
            else:
                tag = "only_right"
            stats[tag] += 1
            self._last_rows.append((oid, lv or "", rv or "", tag))

        self.status.setText(
            _t("Equal: {eq} · Diff: {df} · Only left: {ol} · Only right: {orr}")
            .format(eq=stats["equal"], df=stats["diff"],
                    ol=stats["only_left"], orr=stats["only_right"]))
        self._rerender()

    def _rerender(self) -> None:
        hide_equal = self.hide_equal.isChecked()
        rows = [r for r in getattr(self, "_last_rows", [])
                if not (hide_equal and r[3] == "equal")]
        self.tbl.setRowCount(len(rows))
        for r, (oid, lv, rv, tag) in enumerate(rows):
            oid_str = "." + ".".join(str(x) for x in oid)
            items = [
                QTableWidgetItem(oid_str),
                QTableWidgetItem(lv),
                QTableWidgetItem(rv),
                QTableWidgetItem(tag),
            ]
            # Forcing both bg and fg keeps text readable in every theme.
            bg, fg = {
                "equal":      (QColor("#f5f5f5"), QColor("#555")),
                "only_left":  (QColor("#1565c0"), QColor("white")),
                "only_right": (QColor("#ef6c00"), QColor("white")),
                "diff":       (QColor("#c62828"), QColor("white")),
            }[tag]
            for c, it in enumerate(items):
                it.setBackground(QBrush(bg))
                it.setForeground(QBrush(fg))
                self.tbl.setItem(r, c, it)

    def _export_csv(self) -> None:
        import csv
        rows = getattr(self, "_last_rows", None)
        if not rows:
            return
        path, _sel = QFileDialog.getSaveFileName(
            self, _t("Export CSV"), "compare.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["OID", "Left", "Right", "Status"])
            for oid, lv, rv, tag in rows:
                w.writerow(["." + ".".join(str(x) for x in oid), lv, rv, tag])

    def closeEvent(self, ev) -> None:
        self._stop()
        for t in (self._thread_l, self._thread_r):
            if t is not None and t.isRunning():
                t.wait(800)
        super().closeEvent(ev)
