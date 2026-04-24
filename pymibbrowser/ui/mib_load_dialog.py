"""
Dialog for loading MIB modules: compiles named modules via pysmi (which
transitively resolves IMPORTS from local sources + mibs.pysnmp.com), shows
a per-module status table, and lets the user retry after adding missing
dependency sources.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from .. import config, mib_loader
from ..i18n import _t


STATUS_COLORS = {
    "compiled":    QColor("#6f6"),
    "untouched":   QColor("#bbb"),
    "failed":      QColor("#f66"),
    "missing":     QColor("#fa3"),
    "borrowed":    QColor("#6cf"),
    "unprocessed": QColor("#bbb"),
}


class _CompileWorker(QObject):
    progress = pyqtSignal(str, str, int, int)  # module, status, done, total
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, modules: list[str], extra_dirs: list[Path],
                 use_network: bool) -> None:
        super().__init__()
        self._modules = modules
        self._extra = extra_dirs
        self._network = use_network

    def run(self) -> None:
        try:
            # compile_modules uses one big .compile(*mods) call which doesn't
            # emit per-module progress. Loop ourselves so we can report.
            from pymibbrowser import mib_loader, config
            dest = config.compiled_mibs_dir()
            src_dirs = list(self._extra) + [config.default_mibs_src()]
            compiler = mib_loader._make_compiler(
                src_dirs, dest, use_network=self._network)
            merged: dict = {}
            total = len(self._modules)
            for i, mod in enumerate(self._modules, 1):
                try:
                    res = compiler.compile(mod, rebuild=True, genTexts=True,
                                           ignoreErrors=True)
                except Exception as exc:
                    res = {mod: f"failed: {exc}"}
                merged.update(res)
                self.progress.emit(mod, str(res.get(mod, "")), i, total)
            self.done.emit(dict(merged))
        except Exception as exc:
            self.failed.emit(str(exc))


class MibLoadDialog(QDialog):
    def __init__(self, initial_modules: list[str], parent=None,
                 extra_dirs: list[Path] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Load MIB…"))
        self.resize(620, 460)
        self._modules = list(initial_modules)
        self._extra_dirs: list[Path] = list(extra_dirs or [])
        self._active: tuple[QThread, _CompileWorker] | None = None

        v = QVBoxLayout(self)

        v.addWidget(QLabel(
            f"Compiling {len(self._modules)} module(s) and their IMPORTS. "
            f"Missing dependencies are fetched from mibs.pysnmp.com if "
            f"online, or resolved from additional source directories."))

        self.result_tbl = QTableWidget(0, 2)
        self.result_tbl.setHorizontalHeaderLabels(("Module", "Status"))
        self.result_tbl.horizontalHeader().setStretchLastSection(True)
        self.result_tbl.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.result_tbl.verticalHeader().setVisible(False)
        v.addWidget(self.result_tbl, 1)

        self.net_chk = QCheckBox("Fetch missing deps from mibs.pysnmp.com")
        # Default follows the user's global preference (off by default — so
        # the app doesn't silently go to the internet on every Load MIB).
        from .. import config as _cfg
        try:
            s = _cfg.AppSettings.load()
            self.net_chk.setChecked(s.fetch_missing_from_net)
        except Exception:
            self.net_chk.setChecked(False)
        v.addWidget(self.net_chk)

        hb = QHBoxLayout()
        add_dir_b = QPushButton("Add source directory…")
        add_dir_b.clicked.connect(self._add_dir)
        hb.addWidget(add_dir_b)

        self.extras_lbl = QLabel("")
        self.extras_lbl.setWordWrap(True)
        hb.addWidget(self.extras_lbl, 1)
        v.addLayout(hb)
        self._refresh_extras_label()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close)
        self.compile_btn = QPushButton("Compile")
        self.compile_btn.setDefault(True)
        self.compile_btn.clicked.connect(self._start_compile)
        btns.addButton(self.compile_btn, QDialogButtonBox.ButtonRole.ActionRole)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

        from PyQt6.QtWidgets import QProgressBar
        self.pbar = QProgressBar()
        self.pbar.setTextVisible(True)
        self.pbar.setFormat("%v / %m (%p%)")
        self.pbar.hide()
        v.addWidget(self.pbar)

        self.status_lbl = QLabel("")
        v.addWidget(self.status_lbl)

        # Auto-start compile on open for convenience.
        self._start_compile()

    # ------------------------------------------------------------------

    def _add_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Select MIB source directory")
        if not d:
            return
        p = Path(d)
        if p not in self._extra_dirs:
            self._extra_dirs.append(p)
        self._refresh_extras_label()

    def _refresh_extras_label(self) -> None:
        if not self._extra_dirs:
            self.extras_lbl.setText("No extra source dirs.")
            return
        self.extras_lbl.setText(
            "Source dirs: " + " · ".join(str(x) for x in self._extra_dirs))

    def _start_compile(self) -> None:
        if self._active is not None:
            return
        self.compile_btn.setEnabled(False)
        self.status_lbl.setText("Compiling…")
        self.pbar.setRange(0, len(self._modules) or 1)
        self.pbar.setValue(0)
        self.pbar.show()

        t = QThread(self)
        w = _CompileWorker(self._modules, self._extra_dirs,
                           self.net_chk.isChecked())
        w.moveToThread(t)
        t._worker_ref = w
        t.started.connect(w.run)
        w.progress.connect(self._on_progress)
        w.done.connect(self._on_done)
        w.failed.connect(self._on_failed)
        w.done.connect(t.quit)
        w.failed.connect(t.quit)
        t.finished.connect(w.deleteLater)
        t.finished.connect(t.deleteLater)
        t.finished.connect(self._on_finished_cleanup)
        t.start()
        self._active = (t, w)

    def _on_progress(self, module: str, status: str, done: int, total: int) -> None:
        self.pbar.setRange(0, total)
        self.pbar.setValue(done)
        self.status_lbl.setText(f"[{done}/{total}] {module} — {status}")

    def _on_done(self, results: dict) -> None:
        # Populate the status table. pysmi emits entries for deps too.
        bold = QFont(); bold.setBold(True)
        self.result_tbl.setRowCount(len(results))
        for r, (name, status) in enumerate(sorted(results.items())):
            ni = QTableWidgetItem(name)
            if name in self._modules:
                ni.setFont(bold)
            self.result_tbl.setItem(r, 0, ni)
            si = QTableWidgetItem(str(status))
            color = STATUS_COLORS.get(str(status))
            if color is not None:
                si.setForeground(color)
            self.result_tbl.setItem(r, 1, si)

        compiled = sum(1 for s in results.values() if s == "compiled")
        missing = sum(1 for s in results.values() if s == "missing")
        failed = sum(1 for s in results.values() if s == "failed")
        self.status_lbl.setText(
            f"Done: {compiled} compiled · {missing} missing · {failed} failed")

    def _on_failed(self, msg: str) -> None:
        self.status_lbl.setText(f"Error: {msg}")
        QMessageBox.critical(self, _t("Load MIB…"), msg)

    def _on_finished_cleanup(self) -> None:
        self._active = None
        self.compile_btn.setEnabled(True)
        self.pbar.hide()
