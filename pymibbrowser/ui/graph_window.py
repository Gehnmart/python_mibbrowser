"""Real-time performance graph for a numerical OID."""
from __future__ import annotations

import csv
import time
from collections import deque

import pyqtgraph as pg
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import snmp_ops, workers
from ..config import Agent
from ..i18n import _t

MAX_POINTS = 600


class GraphTab(QWidget):
    def __init__(self, parent, agent: Agent, oid: tuple[int, ...], label: str,
                 tree=None) -> None:
        super().__init__(parent)
        self.agent = agent
        self.oid = oid
        self.label = label
        self.tree = tree
        self._max_points = MAX_POINTS

        self._t: deque = deque(maxlen=self._max_points)
        self._v: deque = deque(maxlen=self._max_points)
        self._last_raw_t: float | None = None
        self._last_raw_v: float | None = None
        self._paused = False
        self._rate_mode = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._active_threads: list = []

        self._build_ui()
        self._start()

    # UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)

        tb = QToolBar()
        v.addWidget(tb)

        # Editable OID inline — lets the user switch targets without closing
        # the tab and re-opening a new Graph each time.
        tb.addWidget(QLabel(_t("OID") + ": "))
        self.oid_edit = QLineEdit(self.label)
        self.oid_edit.setMinimumWidth(220)
        self.oid_edit.returnPressed.connect(self._change_oid)
        tb.addWidget(self.oid_edit)
        apply_b = QPushButton(_t("Apply")); apply_b.clicked.connect(self._change_oid)
        tb.addWidget(apply_b)

        tb.addSeparator()
        self.pause_btn = QPushButton(_t("⏸ Pause")); self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._toggle_pause); tb.addWidget(self.pause_btn)
        restart_b = QPushButton(_t("↻ Restart")); restart_b.clicked.connect(self._restart); tb.addWidget(restart_b)

        tb.addSeparator()
        tb.addWidget(QLabel(_t(" Interval (s): ")))
        self.interval = QSpinBox(); self.interval.setRange(1, 3600); self.interval.setValue(3)
        self.interval.valueChanged.connect(self._interval_changed); tb.addWidget(self.interval)

        self.rate_chk = QCheckBox("Rate (delta)"); self.rate_chk.toggled.connect(self._rate_toggle)
        tb.addWidget(self.rate_chk)
        self.grid_chk = QCheckBox("Grid"); self.grid_chk.setChecked(True)
        self.grid_chk.toggled.connect(self._grid_toggle); tb.addWidget(self.grid_chk)

        tb.addSeparator()
        png_b = QPushButton(_t("Save PNG")); png_b.clicked.connect(self._save_png); tb.addWidget(png_b)
        csv_b = QPushButton(_t("Export CSV")); csv_b.clicked.connect(self._export_csv); tb.addWidget(csv_b)
        imp_b = QPushButton(_t("Import CSV")); imp_b.clicked.connect(self._import_csv); tb.addWidget(imp_b)

        pg.setConfigOptions(antialias=True, background="w", foreground="k")
        self.plot = pg.PlotWidget(title=f"{self.label}")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "time")
        self.plot.setLabel("left", "value")
        self.plot.getAxis("bottom").setLabel("time (s)")
        self.curve = self.plot.plot([], [], pen=pg.mkPen(color=(20, 120, 200), width=2))
        v.addWidget(self.plot, 1)

        self.status_label = QLabel(_t("—"))
        v.addWidget(self.status_label)

    # Controls ---------------------------------------------------------

    def _start(self) -> None:
        self._timer.start(self.interval.value() * 1000)
        self._poll()  # initial sample

    def _toggle_pause(self, checked: bool) -> None:
        self._paused = checked
        self.pause_btn.setText("▶ Resume" if checked else "⏸ Pause")
        if checked:
            self._timer.stop()
        else:
            self._timer.start(self.interval.value() * 1000)

    def _restart(self) -> None:
        self._t.clear(); self._v.clear()
        self._last_raw_t = self._last_raw_v = None
        self.curve.setData([], [])

    def _interval_changed(self, secs: int) -> None:
        if not self._paused:
            self._timer.start(secs * 1000)

    def _rate_toggle(self, checked: bool) -> None:
        self._rate_mode = checked
        self._restart()

    def _grid_toggle(self, checked: bool) -> None:
        self.plot.showGrid(x=checked, y=checked, alpha=0.25)

    def _change_oid(self) -> None:
        text = self.oid_edit.text().strip()
        if not text:
            return
        # Resolve first so we fail fast on a bogus OID without trashing
        # the existing trace.
        if self.tree is not None:
            resolved = self.tree.resolve_name(text)
            if resolved is None:
                QMessageBox.warning(self, _t("Graph…"),
                                    _t("Cannot resolve OID"))
                return
        else:
            try:
                resolved = tuple(int(p) for p in text.strip(".").split("."))
            except ValueError:
                QMessageBox.warning(self, _t("Graph…"),
                                    _t("Cannot resolve OID"))
                return
        # If we've collected real data, make the user confirm before the
        # trace is wiped — accidental Apply was a documented footgun.
        SAMPLE_THRESHOLD = 5
        if len(self._v) >= SAMPLE_THRESHOLD:
            btn = QMessageBox.question(
                self, _t("Graph…"),
                _t("This will discard {n} samples of the current trace.\n"
                   "Export CSV first if you want to keep them.\n\n"
                   "Continue?").format(n=len(self._v)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if btn != QMessageBox.StandardButton.Yes:
                # Put the old OID text back so the user isn't left wondering
                # why the field looks different from the trace.
                self.oid_edit.setText(self.label)
                return
        self.oid = resolved
        self.label = text
        self.plot.setTitle(text)
        self._restart()

    # SNMP polling ----------------------------------------------------

    def _poll(self) -> None:
        if self._paused:
            return
        workers.prune_threads(self._active_threads)
        def on_finished(vbs):
            if not vbs:
                return
            vb = vbs[0]
            try:
                raw = float(vb.display_value)
            except (TypeError, ValueError):
                self.status_label.setText(f"Non-numeric: {vb.display_value!r}")
                return
            now = time.time()
            if self._rate_mode:
                if self._last_raw_t is not None:
                    dt = now - self._last_raw_t
                    val = (raw - self._last_raw_v) / dt if dt > 0 else 0
                else:
                    self._last_raw_t, self._last_raw_v = now, raw
                    return
                self._last_raw_t, self._last_raw_v = now, raw
            else:
                val = raw
            if not self._t:
                self._t0 = now
            self._t.append(now - self._t0)
            self._v.append(val)
            self.curve.setData(list(self._t), list(self._v))
            self.status_label.setText(f"latest={val:g}  samples={len(self._v)}")
        def on_failed(msg):
            self.status_label.setText(f"error: {msg}")
        t, _w = workers.run_op(self, snmp_ops.op_get, on_finished, on_failed, None,
                                self.agent, [self.oid])
        self._active_threads.append(t)

    # Export -----------------------------------------------------------

    def _save_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save PNG", "graph.png", "PNG (*.png)")
        if not path:
            return
        exporter = pg.exporters.ImageExporter(self.plot.plotItem)
        exporter.export(path)

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "graph.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_seconds", "value"])
            for t, v in zip(self._t, self._v, strict=False):
                w.writerow([f"{t:.3f}", v])

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import CSV", "", "CSV (*.csv)")
        if not path:
            return
        ts: list[float] = []; vs: list[float] = []
        with open(path, newline="") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                try:
                    ts.append(float(row[0])); vs.append(float(row[1]))
                except Exception:
                    continue
        self._timer.stop()
        self._paused = True
        self.pause_btn.setChecked(True)
        self._t = deque(ts, maxlen=self._max_points)
        self._v = deque(vs, maxlen=self._max_points)
        self.curve.setData(list(self._t), list(self._v))

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
