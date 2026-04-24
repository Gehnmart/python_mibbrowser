"""Real-time performance graph for a numerical OID."""
from __future__ import annotations

import csv
import time
from collections import deque
from typing import Optional

import pyqtgraph as pg
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSpinBox, QToolBar, QVBoxLayout, QWidget,
)

from .. import snmp_ops, workers
from ..config import Agent


MAX_POINTS = 600


class GraphTab(QWidget):
    def __init__(self, parent, agent: Agent, oid: tuple[int, ...], label: str) -> None:
        super().__init__(parent)
        self.agent = agent
        self.oid = oid
        self.label = label
        self._max_points = MAX_POINTS

        self._t: deque = deque(maxlen=self._max_points)
        self._v: deque = deque(maxlen=self._max_points)
        self._last_raw_t: Optional[float] = None
        self._last_raw_v: Optional[float] = None
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
        self.pause_btn = QPushButton("⏸ Pause"); self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._toggle_pause); tb.addWidget(self.pause_btn)
        restart_b = QPushButton("↻ Restart"); restart_b.clicked.connect(self._restart); tb.addWidget(restart_b)

        tb.addSeparator()
        tb.addWidget(QLabel(" Interval (s): "))
        self.interval = QSpinBox(); self.interval.setRange(1, 3600); self.interval.setValue(3)
        self.interval.valueChanged.connect(self._interval_changed); tb.addWidget(self.interval)

        self.rate_chk = QCheckBox("Rate (delta)"); self.rate_chk.toggled.connect(self._rate_toggle)
        tb.addWidget(self.rate_chk)
        self.grid_chk = QCheckBox("Grid"); self.grid_chk.setChecked(True)
        self.grid_chk.toggled.connect(self._grid_toggle); tb.addWidget(self.grid_chk)

        tb.addSeparator()
        png_b = QPushButton("Save PNG"); png_b.clicked.connect(self._save_png); tb.addWidget(png_b)
        csv_b = QPushButton("Export CSV"); csv_b.clicked.connect(self._export_csv); tb.addWidget(csv_b)
        imp_b = QPushButton("Import CSV"); imp_b.clicked.connect(self._import_csv); tb.addWidget(imp_b)

        pg.setConfigOptions(antialias=True, background="w", foreground="k")
        self.plot = pg.PlotWidget(title=f"{self.label}")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "time")
        self.plot.setLabel("left", "value")
        self.plot.getAxis("bottom").setLabel("time (s)")
        self.curve = self.plot.plot([], [], pen=pg.mkPen(color=(20, 120, 200), width=2))
        v.addWidget(self.plot, 1)

        self.status_label = QLabel("—")
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

    # SNMP polling ----------------------------------------------------

    def _poll(self) -> None:
        if self._paused:
            return
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
            for t, v in zip(self._t, self._v):
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
