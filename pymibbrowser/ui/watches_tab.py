"""Watches — live monitoring of a set of OIDs with a per-row 'normal-state'
predicate. Matches iReasoning's Watches feature (Add to Watches dialog +
Watches tab with colored rows).

The tab periodically GETs each watch against the currently-selected
agent (toolbar), compares the result to the configured condition, and
colors the row:
  • green  — condition holds (normal)
  • red    — condition false (alarm)
  • grey   — fetching / error

State transitions (normal ↔ alarm) are logged persistently to
data_dir()/watches_history.jsonl — so restarting the app doesn't lose
the record of what went wrong last night.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import workers
from ..infra import config, snmp_ops
from ..infra.config import AppSettings, WatchDefinition
from ..infra.i18n import _t


def _history_path() -> Path:
    return config.data_dir() / "watches_history.jsonl"


def _append_history_event(event: dict) -> None:
    """JSONL append; tolerant to failures — we don't want a disk hiccup
    to crash the polling loop."""
    try:
        with open(_history_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _read_history(limit: int = 5000) -> list[dict]:
    p = _history_path()
    if not p.exists():
        return []
    lines: list[dict] = []
    try:
        with open(p, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    lines.append(json.loads(raw))
                except Exception:
                    continue
    except Exception:
        return []
    if len(lines) > limit:
        lines = lines[-limit:]
    return lines


OP_CHOICES = ("Get", "Get Next")
COND_CHOICES = (">", ">=", "<", "<=", "==", "!=")
# Inverse of each comparison op — used to spell out the alarm side of the
# predicate in the dialog ("Normal: > 110  ·  Alarm: ≤ 110") so the user
# can see both halves at once.
_INVERSE_OP = {
    ">": "≤", ">=": "<", "<": "≥", "<=": ">",
    "==": "≠", "!=": "=",
}


def _coerce_numeric(s) -> float | None:
    """Best-effort number extraction from an SNMP display value.

    Plain ints/floats parse directly. TimeTicks render as
    ``"1 hour 50.00 seconds (660000)"`` — pull the trailing parenthesised
    raw count, which is the unit users actually thresholding on.
    Otherwise fall back to a leading numeric token (e.g. ``"111 packets"``
    → 111). Returns None if nothing numeric can be salvaged."""
    import re
    text = str(s).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    m = re.search(r"\(\s*([+-]?\d+(?:\.\d+)?)\s*\)\s*$", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    m = re.match(r"^[+-]?\d+(?:\.\d+)?", text)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            pass
    return None


def _evaluate_condition(value: str, op: str, threshold: str) -> bool | None:
    """Return True if condition holds, False if not, None if not comparable.

    Tries numeric comparison first — including light coercion so values
    with units (``"111 packets"``) or TimeTicks (``"... (660000)"``)
    still threshold sensibly. Falls back to string compare for == / !=
    only — you can't meaningfully 'greater than' an OctetString."""
    lv = _coerce_numeric(value)
    rv = _coerce_numeric(threshold)
    if lv is not None and rv is not None:
        return {
            ">": lv > rv, ">=": lv >= rv,
            "<": lv < rv, "<=": lv <= rv,
            "==": lv == rv, "!=": lv != rv,
        }.get(op)
    sval, sthr = str(value).strip(), str(threshold).strip()
    if op == "==":
        return sval == sthr
    if op == "!=":
        return sval != sthr
    return None


class AddWatchDialog(QDialog):
    """Add / Edit a single WatchDefinition.

    Pre-filling `seed.oid` from the tree context menu gets the Name
    autocompleted to the symbolic node name if we can resolve it."""

    def __init__(self, seed: WatchDefinition, tree=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Add to Watches"))
        self.setMinimumWidth(460)
        self.tree = tree
        self.result_watch = WatchDefinition(**vars(seed))

        form = QFormLayout(self)
        self.oid_edit = QLineEdit(seed.oid)
        self.name_edit = QLineEdit(seed.name)
        self.op_combo = QComboBox(); self.op_combo.addItems(OP_CHOICES)
        self.op_combo.setCurrentText(seed.operation or "Get")

        # Condition row: label "Normal state if result", op, value.
        cond_row = QWidget()
        crow = QHBoxLayout(cond_row); crow.setContentsMargins(0, 0, 0, 0)
        self.cond_op = QComboBox(); self.cond_op.addItems(COND_CHOICES)
        self.cond_op.setCurrentText(seed.condition_op or ">")
        self.cond_val = QLineEdit(seed.condition_value)
        crow.addWidget(self.cond_op); crow.addWidget(self.cond_val, 1)

        # Live-updated hint that spells out the alarm side of the
        # predicate too. The bare "Normal state if result > 110" wording
        # was misread as "alarm if > 110", so make both sides explicit.
        self.cond_hint = QLabel("")
        self.cond_hint.setStyleSheet("color: #666; font-style: italic;")
        self.cond_hint.setWordWrap(True)
        self.cond_op.currentTextChanged.connect(self._update_cond_hint)
        self.cond_val.textChanged.connect(self._update_cond_hint)

        form.addRow("OID:", self.oid_edit)
        form.addRow(_t("Name") + ":", self.name_edit)
        form.addRow(_t("SNMP Operation") + ":", self.op_combo)
        form.addRow(_t("Normal state if result") + ":", cond_row)
        form.addRow("", self.cond_hint)
        self._update_cond_hint()

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _update_cond_hint(self) -> None:
        op = self.cond_op.currentText()
        val = self.cond_val.text().strip()
        if not val:
            self.cond_hint.setText(_t(
                "Tip: enter the value the result must satisfy to count "
                "as normal — anything else is an alarm."))
            return
        inv = _INVERSE_OP.get(op, op)
        self.cond_hint.setText(_t(
            "Normal: result {op} {val}    ·    Alarm: result {inv} {val}"
        ).format(op=op, val=val, inv=inv))

    def _accept(self) -> None:
        oid = self.oid_edit.text().strip()
        name = self.name_edit.text().strip()
        if not oid:
            QMessageBox.warning(self, _t("Add to Watches"),
                                _t("OID is required."))
            return
        if self.tree is not None:
            resolved = self.tree.resolve_name(oid)
            if resolved is None:
                QMessageBox.warning(self, _t("Add to Watches"),
                                    _t("Cannot resolve OID"))
                return
            if not name:
                name = oid
            oid = "." + ".".join(str(x) for x in resolved)
        if not name:
            name = oid
        self.result_watch = WatchDefinition(
            name=name, oid=oid,
            operation=self.op_combo.currentText(),
            condition_op=self.cond_op.currentText(),
            condition_value=self.cond_val.text().strip(),
        )
        self.accept()


class WatchesTab(QWidget):
    """Live watches table + shared polling timer.

    The tab reads settings.watches at construction and watches the
    same list thereafter (so Add/Remove in the toolbar buttons mutates
    settings and refills the table). Agent is whichever one the main
    window currently uses, pulled through a callable."""

    def __init__(self, parent, settings: AppSettings, current_agent_fn,
                 tree=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._current_agent_fn = current_agent_fn
        self.tree = tree
        self._paused = False
        self._active_threads: list = []
        # Remember last evaluated state per-watch (by index) so we can
        # detect ok→alarm / alarm→ok transitions and log them. Value is
        # Optional[bool] — matches _evaluate_condition's return type.
        self._last_state: dict[int, bool | None] = {}

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)

        self._build_ui()
        self._refill()
        self._refresh()
        self._timer.start(max(1, settings.watch_interval_s) * 1000)

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)

        tb = QToolBar()
        refresh_b = QPushButton(_t("Refresh"))
        refresh_b.clicked.connect(self._refresh)
        tb.addWidget(refresh_b)
        self.pause_btn = QPushButton(_t("⏸ Pause"))
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._toggle_pause)
        tb.addWidget(self.pause_btn)

        tb.addSeparator()
        add_b = QPushButton("+ " + _t("Add"))
        add_b.clicked.connect(self._add_watch)
        tb.addWidget(add_b)
        edit_b = QPushButton(_t("Edit"))
        edit_b.clicked.connect(self._edit_watch)
        tb.addWidget(edit_b)
        rm_b = QPushButton(_t("Remove"))
        rm_b.clicked.connect(self._remove_watch)
        tb.addWidget(rm_b)

        tb.addSeparator()
        tb.addWidget(QLabel(_t("Interval:") + " "))
        self.interval_edit = QSpinBox()
        self.interval_edit.setRange(1, 3600)
        self.interval_edit.setValue(self.settings.watch_interval_s)
        self.interval_edit.setSuffix(" s")
        self.interval_edit.valueChanged.connect(self._on_interval_changed)
        tb.addWidget(self.interval_edit)

        tb.addSeparator()
        hist_b = QPushButton(_t("History…"))
        hist_b.clicked.connect(self._show_history)
        tb.addWidget(hist_b)
        exp_b = QPushButton(_t("Export CSV"))
        exp_b.clicked.connect(self._export_csv)
        tb.addWidget(exp_b)
        v.addWidget(tb)

        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels([
            _t("Name"), _t("Value"), _t("Condition"),
            _t("Status"), _t("Last Query"),
        ])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.doubleClicked.connect(lambda _=None: self._edit_watch())
        # Stretch the last column. setStretchLastSection is convenient
        # but only re-applies on the *next* header resize event — so a
        # subsequent resizeColumnsToContents() in _refill leaves the
        # table narrower than the tab until the user nudges a column.
        # Stretch resize-mode is sticky: Qt restretches automatically on
        # every header geometry change.
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(
            self.tbl.columnCount() - 1, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tbl, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666;")
        v.addWidget(self.status_label)

    # --- table refill -------------------------------------------------

    def _refill(self, select: int = -1) -> None:
        self.tbl.setRowCount(len(self.settings.watches))
        for r, w in enumerate(self.settings.watches):
            self.tbl.setItem(r, 0, QTableWidgetItem(w.name))
            # Value / Status filled in by refresh; leave blanks.
            self.tbl.setItem(r, 1, QTableWidgetItem(""))
            self.tbl.setItem(
                r, 2, QTableWidgetItem(
                    f"{w.condition_op} {w.condition_value}"
                    if w.condition_value != "" else ""))
            self.tbl.setItem(r, 3, QTableWidgetItem(""))
            self.tbl.setItem(r, 4, QTableWidgetItem(""))
        # Size every column except the last (which is in Stretch mode and
        # auto-fills) — calling resizeColumnsToContents() on the whole
        # table would shrink the stretched section too and the table
        # would stop reaching the right edge of the tab.
        for c in range(self.tbl.columnCount() - 1):
            self.tbl.resizeColumnToContents(c)
        if 0 <= select < len(self.settings.watches):
            self.tbl.selectRow(select)

    def _selected(self) -> int:
        r = self.tbl.currentRow()
        return r if 0 <= r < len(self.settings.watches) else -1

    # --- controls ------------------------------------------------------

    def _toggle_pause(self, checked: bool) -> None:
        self._paused = checked
        self.pause_btn.setText(
            _t("▶ Resume") if checked else _t("⏸ Pause"))
        if checked:
            self._timer.stop()
        else:
            self._timer.start(max(1, self.settings.watch_interval_s) * 1000)
            self._refresh()

    def _on_interval_changed(self, v: int) -> None:
        self.settings.watch_interval_s = int(v)
        config.save_settings(self.settings)
        if not self._paused:
            self._timer.start(max(1, v) * 1000)

    def _add_watch(self) -> None:
        d = AddWatchDialog(WatchDefinition(), tree=self.tree, parent=self)
        if d.exec():
            self.settings.watches.append(d.result_watch)
            config.save_settings(self.settings)
            self._refill(select=len(self.settings.watches) - 1)
            self._refresh()

    def _edit_watch(self) -> None:
        r = self._selected()
        if r < 0:
            return
        d = AddWatchDialog(self.settings.watches[r],
                           tree=self.tree, parent=self)
        if d.exec():
            self.settings.watches[r] = d.result_watch
            config.save_settings(self.settings)
            self._refill(select=r)
            self._refresh()

    def _remove_watch(self) -> None:
        r = self._selected()
        if r < 0:
            return
        del self.settings.watches[r]
        config.save_settings(self.settings)
        # Drop just the deleted row from the table. _refill would
        # rebuild every row from settings.watches and blank out Value/
        # Status/Last Query for the survivors until the next poll
        # tick — visually as if every other watch had also reset.
        self.tbl.removeRow(r)
        # _last_state is keyed by row index. Removing row `r` shifts
        # everything below up by one, so reindex the dict to keep
        # transition logging mapped to the right watches.
        self._last_state = {
            (k if k < r else k - 1): v
            for k, v in self._last_state.items()
            if k != r
        }
        new_sel = min(r, self.tbl.rowCount() - 1)
        if new_sel >= 0:
            self.tbl.selectRow(new_sel)

    # --- refresh ------------------------------------------------------

    def _refresh(self) -> None:
        if self._paused:
            return
        workers.prune_threads(self._active_threads)
        agent = self._current_agent_fn()
        stamp = datetime.now().strftime("%H:%M:%S")
        for r, w in enumerate(self.settings.watches):
            oid = w.oid
            if self.tree is not None:
                t = self.tree.resolve_name(oid)
                if t is None:
                    try:
                        t = tuple(int(p) for p in oid.strip(".").split("."))
                    except ValueError:
                        t = ()
            else:
                try:
                    t = tuple(int(p) for p in oid.strip(".").split("."))
                except ValueError:
                    t = ()
            if not t:
                self._paint_row(r, "?", _t("invalid oid"), None, stamp)
                continue
            self._dispatch(r, w, agent, t, stamp)

        self.status_label.setText(
            _t("Watching {n} OIDs · {stamp}").format(
                n=len(self.settings.watches), stamp=stamp))

    def _dispatch(self, row: int, w: WatchDefinition, agent,
                  oid: tuple[int, ...], stamp: str) -> None:
        fn = (snmp_ops.op_next if w.operation == "Get Next"
              else snmp_ops.op_get)

        def on_finished(vbs):
            if not vbs:
                self._paint_row(row, "—", _t("no data"), None, stamp)
                self._record_transition(row, w, None, "—", agent)
                return
            val = vbs[0].display_value
            ok = _evaluate_condition(val, w.condition_op, w.condition_value)
            status = (_t("normal") if ok is True
                      else _t("alarm") if ok is False
                      else _t("n/a"))
            self._paint_row(row, str(val), status, ok, stamp)
            self._record_transition(row, w, ok, str(val), agent)

        def on_failed(msg):
            self._paint_row(row, f"error: {msg[:30]}",
                            _t("error"), None, stamp)
            self._record_transition(row, w, None, f"error: {msg}", agent)

        t, _w = workers.run_op(
            self, fn, on_finished, on_failed, None, agent, [oid])
        self._active_threads.append(t)

    def _record_transition(self, row: int, w: WatchDefinition,
                           new_state: bool | None, value: str,
                           agent) -> None:
        """Log if state changed since last eval. Only transitions —
        logging every tick would drown the file in duplicates. First
        observation also counts as a transition (from unknown)."""
        prev = self._last_state.get(row, "__unset__")
        self._last_state[row] = new_state
        if prev == new_state and prev != "__unset__":
            return
        event = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "watch": w.name,
            "oid": w.oid,
            "agent": f"{agent.host}:{agent.port}",
            "value": value,
            "state": (
                "normal" if new_state is True
                else "alarm" if new_state is False
                else "n/a"
            ),
            "from": (
                "normal" if prev is True
                else "alarm" if prev is False
                else "n/a" if prev is None
                else "unknown"
            ),
            "condition": f"{w.condition_op} {w.condition_value}",
        }
        _append_history_event(event)

    def _show_history(self) -> None:
        HistoryDialog(self).exec()

    def _paint_row(self, row: int, value: str, status: str,
                   ok: bool | None, stamp: str) -> None:
        """Colour the row. Forcing both foreground and background is
        required — a Qt theme-controlled text colour (white on dark
        themes) vs a light pastel background makes the text invisible,
        which was exactly the 'pink row with no text' bug."""
        if row >= self.tbl.rowCount():
            return
        self.tbl.item(row, 1).setText(value)
        self.tbl.item(row, 3).setText(status)
        self.tbl.item(row, 4).setText(stamp)
        if ok is True:
            bg_color = QColor("#2e7d32")   # Material Green 800
            fg_color = QColor("white")
        elif ok is False:
            bg_color = QColor("#c62828")   # Material Red 800
            fg_color = QColor("white")
        else:
            bg_color = QColor("#616161")   # Material Grey 700
            fg_color = QColor("white")
        bg = QBrush(bg_color)
        fg = QBrush(fg_color)
        for c in range(self.tbl.columnCount()):
            it = self.tbl.item(row, c)
            if it is not None:
                it.setBackground(bg)
                it.setForeground(fg)

    # --- export -------------------------------------------------------

    def _export_csv(self) -> None:
        import csv
        path, _sel = QFileDialog.getSaveFileName(
            self, _t("Export CSV"), "watches.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow([self.tbl.horizontalHeaderItem(c).text()
                         for c in range(self.tbl.columnCount())])
            for r in range(self.tbl.rowCount()):
                wr.writerow([self.tbl.item(r, c).text() if self.tbl.item(r, c)
                             else "" for c in range(self.tbl.columnCount())])

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# History dialog — reads watches_history.jsonl and shows a filterable
# table of transitions. Lets you answer "when did the backup server
# first go down?" without grepping log files.
# ---------------------------------------------------------------------------

class HistoryDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Watch history"))
        self.resize(820, 520)

        v = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel(_t("Filter") + ":"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(
            _t("Substring in any column"))
        self.filter_edit.textChanged.connect(self._apply_filter)
        row.addWidget(self.filter_edit, 1)
        clear_b = QPushButton(_t("Clear history"))
        clear_b.clicked.connect(self._clear_history)
        row.addWidget(clear_b)
        v.addLayout(row)

        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels([
            _t("Time"), _t("Watch"), _t("Agent"),
            _t("From") + " → " + _t("State"),
            _t("Value"), _t("Condition"),
        ])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.horizontalHeader().setSectionResizeMode(
            self.tbl.columnCount() - 1, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tbl, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #666;")
        v.addWidget(self.status)

        btns = QDialogButtonBox()
        exp_b = QPushButton(_t("Export CSV"))
        exp_b.clicked.connect(self._export_csv)
        btns.addButton(exp_b, QDialogButtonBox.ButtonRole.ActionRole)
        close_b = QPushButton(_t("Close"))
        close_b.clicked.connect(self.reject)
        btns.addButton(close_b, QDialogButtonBox.ButtonRole.RejectRole)
        v.addWidget(btns)

        self._events = _read_history()
        self._render()

    def _render(self) -> None:
        needle = self.filter_edit.text().lower().strip()
        rows = [ev for ev in self._events
                if not needle or self._matches(ev, needle)]
        # Newest first — usual expectation for "history".
        rows.reverse()
        self.tbl.setRowCount(len(rows))
        for r, ev in enumerate(rows):
            trans = f"{ev.get('from','?')} → {ev.get('state','?')}"
            cells = [
                ev.get("ts", ""),
                ev.get("watch", ""),
                ev.get("agent", ""),
                trans,
                ev.get("value", ""),
                ev.get("condition", ""),
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                # Colour the state column so alarms stand out.
                if c == 3:
                    state = ev.get("state")
                    if state == "alarm":
                        item.setBackground(QBrush(QColor("#c62828")))
                        item.setForeground(QBrush(QColor("white")))
                    elif state == "normal":
                        item.setBackground(QBrush(QColor("#2e7d32")))
                        item.setForeground(QBrush(QColor("white")))
                self.tbl.setItem(r, c, item)
        for c in range(self.tbl.columnCount() - 1):
            self.tbl.resizeColumnToContents(c)
        self.status.setText(
            _t("{shown} of {total} event(s)").format(
                shown=len(rows), total=len(self._events)))

    def _matches(self, ev: dict, needle: str) -> bool:
        return any(needle in str(ev.get(k, "")).lower()
                   for k in ("ts", "watch", "agent", "value",
                             "state", "from", "condition", "oid"))

    def _apply_filter(self, _text: str) -> None:
        self._render()

    def _clear_history(self) -> None:
        btn = QMessageBox.question(
            self, _t("Clear history"),
            _t("Delete all recorded watch transitions?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if btn != QMessageBox.StandardButton.Yes:
            return
        try:
            _history_path().unlink(missing_ok=True)
        except Exception:
            pass
        self._events = []
        self._render()

    def _export_csv(self) -> None:
        import csv
        path, _sel = QFileDialog.getSaveFileName(
            self, _t("Export CSV"), "watch_history.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time", "watch", "agent", "from", "state",
                        "value", "condition", "oid"])
            for ev in self._events:
                w.writerow([
                    ev.get("ts", ""), ev.get("watch", ""),
                    ev.get("agent", ""), ev.get("from", ""),
                    ev.get("state", ""), ev.get("value", ""),
                    ev.get("condition", ""), ev.get("oid", ""),
                ])
