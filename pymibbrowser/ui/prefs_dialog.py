"""Preferences dialog — central place for settings that were scattered
across Advanced (agent), Rebuild MIB, Trap Receiver port field, and
Help → Language.

Save applies everything to AppSettings and returns True so the main
window can refresh dependent widgets.

Each control is paired with a help-string that shows up in a status
strip at the bottom of the dialog whenever the control (or its label)
is hovered / focused — so arcane option names like 'Lenient MIB parser'
or 'Single root (.iso)' are explained without stuffing the form with
block quotes.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget, QFileDialog,
)

from .. import config
from ..i18n import _t


LANG_CHOICES = (("(auto — $LANG)", ""), ("English", "en"), ("Русский", "ru"))
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class _HintFilter(QObject):
    """Watches Enter/FocusIn events on registered widgets and pushes
    the widget's help-string to the dialog's status strip."""

    def __init__(self, dialog: "PreferencesDialog") -> None:
        super().__init__(dialog)
        self._dlg = dialog

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        # Only update on Enter/FocusIn — let the last shown hint linger
        # so the user can actually finish reading it after moving away.
        # It gets replaced the next time focus/hover lands somewhere
        # else with a hint.
        if ev.type() in (QEvent.Type.Enter, QEvent.Type.FocusIn):
            msg = self._dlg._hints.get(id(obj))
            if msg:
                self._dlg._set_hint(msg)
        return False


class PreferencesDialog(QDialog):
    def __init__(self, settings: config.AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Preferences"))
        self.resize(640, 520)
        self.settings = settings

        self._hints: dict[int, str] = {}
        self._hint_filter = _HintFilter(self)

        tabs = QTabWidget(self)

        # --- General -----------------------------------------------------
        g = QWidget(); gl = QFormLayout(g)
        self.lang_combo = QComboBox()
        for label, code in LANG_CHOICES:
            self.lang_combo.addItem(label, code)
        self.lang_combo.setCurrentIndex(
            next((i for i, c in enumerate(LANG_CHOICES)
                  if c[1] == settings.language), 0))
        self._row(gl, _t("Language") + " " + _t("(restart required)"),
                  self.lang_combo,
                  _t("UI language. '(auto)' uses your $LANG env variable; "
                     "the choice is stored and applied on next launch."))

        self.tree_root_chk = QCheckBox(
            _t("Single root (.iso) in MIB tree"))
        self.tree_root_chk.setChecked(settings.single_tree_root)
        self._row(gl, "", self.tree_root_chk,
                  _t("When on, the tree starts from a single iso (.1) root. "
                     "When off, the top level lists iso's immediate children "
                     "(org, etc.) side-by-side — useful if you always work "
                     "inside mgmt.mib-2 and want one less click."))
        tabs.addTab(g, _t("General"))

        # --- SNMP defaults -----------------------------------------------
        # These edit settings.default_agent — the template used for NEW
        # agents (Manage agents → Add, or when the toolbar Address combo
        # needs to create one from scratch). They don't change whoever
        # you're currently talking to; that's what Advanced… / Manage
        # agents is for.
        s = QWidget(); sl = QFormLayout(s)
        banner = QLabel(_t(
            "These are defaults for NEW agents. To edit your current "
            "agent, use the toolbar's Advanced… button or Tools → Manage "
            "agents."))
        banner.setWordWrap(True)
        banner.setStyleSheet("color: #666; font-style: italic; padding: 4px;")
        sl.addRow(banner)
        ag = settings.default_agent
        self.def_ver = QComboBox(); self.def_ver.addItems(("1", "2c", "3"))
        self.def_ver.setCurrentText(ag.version)
        self.def_rcomm = QLineEdit(ag.read_community)
        self.def_wcomm = QLineEdit(ag.write_community)
        self.timeout_edit = QDoubleSpinBox()
        self.timeout_edit.setRange(0.1, 60); self.timeout_edit.setValue(ag.timeout_s)
        self.retries_edit = QSpinBox()
        self.retries_edit.setRange(0, 10); self.retries_edit.setValue(ag.retries)
        self.maxrep_edit = QSpinBox()
        self.maxrep_edit.setRange(1, 200); self.maxrep_edit.setValue(ag.max_repetitions)
        self.nonrep_edit = QSpinBox()
        self.nonrep_edit.setRange(0, 50); self.nonrep_edit.setValue(ag.non_repeaters)

        self._row(sl, _t("Default version"), self.def_ver,
                  _t("SNMP version used when adding a fresh agent. v2c is "
                     "the usual choice; v1 only if the device is ancient."))
        self._row(sl, _t("Default read community"), self.def_rcomm,
                  _t("Read-only community string for GET/WALK. Typically "
                     "'public' on lab devices."))
        self._row(sl, _t("Default write community"), self.def_wcomm,
                  _t("Write community for SET. Keep this secret — avoid "
                     "the default 'private' on production."))
        self._row(sl, _t("Timeout (s)"), self.timeout_edit,
                  _t("How long to wait for each UDP response before giving "
                     "up. Raise for slow links; lower for faster failures."))
        self._row(sl, _t("Retries"), self.retries_edit,
                  _t("Number of times to re-send a request after a timeout "
                     "before declaring the agent unreachable."))
        self._row(sl, _t("Max repetitions (bulk)"), self.maxrep_edit,
                  _t("GETBULK's 'max-repetitions': how many rows the "
                     "agent packs per response. 10–50 is a good range."))
        self._row(sl, _t("Non repeaters (bulk)"), self.nonrep_edit,
                  _t("GETBULK's 'non-repeaters': leading varbinds treated "
                     "as a plain GET. Usually 0."))
        tabs.addTab(s, _t("SNMP"))

        # --- MIB ---------------------------------------------------------
        m = QWidget(); ml = QFormLayout(m)
        self.net_chk = QCheckBox(
            _t("Fetch missing dependencies from mibs.pysnmp.com"))
        self.net_chk.setChecked(settings.fetch_missing_from_net)
        self._row(ml, "", self.net_chk,
                  _t("When compiling a MIB, if an IMPORTS statement names "
                     "a module you don't have locally, download it from "
                     "mibs.pysnmp.com. Off means fully offline — missing "
                     "deps cause the compile to fail."))
        self.lenient_chk = QCheckBox(_t("Lenient MIB parser"))
        self.lenient_chk.setChecked(settings.lenient_mib_parser)
        self._row(ml, "", self.lenient_chk,
                  _t("Accept vendor MIBs that bend SMIv2 rules — missing "
                     "DESCRIPTION clauses, ad-hoc types, reserved words. "
                     "Turn off only if you want a strict validator."))
        mgr_btn = QPushButton(_t("Open MIB Modules manager…"))
        mgr_btn.clicked.connect(self._open_modules)
        self._row(ml, "", mgr_btn,
                  _t("Enable or disable individual compiled modules in the "
                     "MIB tree — useful to hide enterprise noise you don't "
                     "care about."))
        tabs.addTab(m, _t("MIB"))

        # --- Traps -------------------------------------------------------
        tt = QWidget(); tl = QFormLayout(tt)
        self.trap_port_edit = QSpinBox()
        self.trap_port_edit.setRange(1, 65535)
        self.trap_port_edit.setValue(settings.trap_port)
        self._row(tl, _t("Default trap port"), self.trap_port_edit,
                  _t("UDP port the Trap Receiver binds to. The standard "
                     "port is 162 but it requires root; 11162 is safe for "
                     "unprivileged testing."))
        tl.addRow(QLabel(
            _t("Note: ports under 1024 require root. Use 11162+ for tests.")))
        self.trap_accept_edit = QLineEdit(settings.trap_accept_from)
        self.trap_accept_edit.setPlaceholderText(
            "10.0.0.0/8, 192.168.1.5")
        self._row(tl, _t("Accept traps from"), self.trap_accept_edit,
                  _t("Comma-separated list of hosts / CIDRs the Trap "
                     "Receiver will accept from. Empty = accept any "
                     "source. Non-matching datagrams are dropped before "
                     "parsing — DoS-resistant."))
        tabs.addTab(tt, _t("Traps"))

        # --- Graph -------------------------------------------------------
        gr = QWidget(); gl2 = QFormLayout(gr)
        self.max_pts = QSpinBox()
        self.max_pts.setRange(60, 10000)
        self.max_pts.setValue(settings.max_graph_points)
        self._row(gl2, _t("Max graph data points"), self.max_pts,
                  _t("Sliding-window size for real-time graphs. Older "
                     "samples are dropped once this many have been "
                     "collected. Bigger = longer history, more memory."))
        tabs.addTab(gr, _t("Graph"))

        # --- Logging -----------------------------------------------------
        lg = QWidget(); ll = QFormLayout(lg)
        self.log_level = QComboBox()
        self.log_level.addItems(LOG_LEVELS)
        self.log_level.setCurrentText(settings.logging_level)
        self._row(ll, _t("Console log level"), self.log_level,
                  _t("Verbosity on the console/stderr. The file handler "
                     "always writes DEBUG — this only filters terminal "
                     "output."))

        # Log-directory chooser: shows the current effective path and lets
        # the user pick a new one. Takes effect on next launch.
        self.log_dir_edit = QLineEdit(settings.log_dir or str(config.log_dir()))
        self.log_dir_edit.setPlaceholderText(str(config.log_dir()))
        browse_btn = QPushButton(_t("Browse…"))
        browse_btn.clicked.connect(self._pick_log_dir)
        reset_btn = QPushButton(_t("Default"))
        reset_btn.clicked.connect(self._reset_log_dir)
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.log_dir_edit, 1); h.addWidget(browse_btn); h.addWidget(reset_btn)
        self._row(ll, _t("Log directory") + " " + _t("(restart required)"),
                  row,
                  _t("Where rotating log files live. Empty = the default "
                     "under your XDG data directory."))

        ll.addRow(QLabel(_t("Log file:") + f" {config.log_file().name}"))
        open_log_btn = QPushButton(_t("Open log file…"))
        open_log_btn.clicked.connect(self._open_log)
        self._row(ll, "", open_log_btn,
                  _t("Open the current log file in your default viewer."))
        tabs.addTab(lg, _t("Logging"))

        # Layout: tabs on top, status strip + Ok/Cancel at the bottom.
        v = QVBoxLayout(self)
        v.addWidget(tabs, 1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        v.addWidget(sep)

        self.hint_lbl = QLabel("")
        self.hint_lbl.setWordWrap(True)
        self.hint_lbl.setMinimumHeight(48)
        self.hint_lbl.setStyleSheet("color: #666; padding: 4px 2px;")
        self.hint_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.hint_lbl)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    # ------------------------------------------------------------------
    # Hint wiring
    # ------------------------------------------------------------------

    def _row(self, form: QFormLayout, label_text: str, widget: QWidget,
             hint: str) -> None:
        """Add a row, attach `hint` as tooltip on widget + label, and
        register both for the hover/focus → status-strip update."""
        label = QLabel(label_text) if label_text else None
        widget.setToolTip(hint)
        widget.installEventFilter(self._hint_filter)
        self._hints[id(widget)] = hint
        if label is not None:
            label.setToolTip(hint)
            label.installEventFilter(self._hint_filter)
            self._hints[id(label)] = hint
            form.addRow(label, widget)
        else:
            form.addRow(widget)

    def _set_hint(self, msg: str) -> None:
        self.hint_lbl.setText(msg)

    # ------------------------------------------------------------------

    def _accept(self) -> None:
        s = self.settings
        s.language = self.lang_combo.currentData()
        s.single_tree_root = self.tree_root_chk.isChecked()

        # Write to default_agent (the template for NEW agents). Current
        # agent is untouched — the user updates that via Advanced… or the
        # toolbar combo.
        ag = s.default_agent
        ag.timeout_s = float(self.timeout_edit.value())
        ag.retries = int(self.retries_edit.value())
        ag.max_repetitions = int(self.maxrep_edit.value())
        ag.non_repeaters = int(self.nonrep_edit.value())
        ag.version = self.def_ver.currentText()
        ag.read_community = self.def_rcomm.text()
        ag.write_community = self.def_wcomm.text()

        s.fetch_missing_from_net = self.net_chk.isChecked()
        s.lenient_mib_parser = self.lenient_chk.isChecked()
        s.trap_port = int(self.trap_port_edit.value())
        s.trap_accept_from = self.trap_accept_edit.text().strip()
        s.max_graph_points = int(self.max_pts.value())
        s.logging_level = self.log_level.currentText()
        new_log_dir = self.log_dir_edit.text().strip()
        s.log_dir = "" if new_log_dir == str(config.log_dir()) else new_log_dir
        s.save()
        self.accept()

    def _open_modules(self) -> None:
        p = self.parent()
        if p is not None and hasattr(p, "_open_mib_modules"):
            p._open_mib_modules()

    def _open_log(self) -> None:
        p = self.parent()
        if p is not None and hasattr(p, "_open_log_file"):
            p._open_log_file()

    def _pick_log_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, _t("Log directory"), self.log_dir_edit.text())
        if d:
            self.log_dir_edit.setText(d)

    def _reset_log_dir(self) -> None:
        from .. import config as cfg
        prev = cfg._log_dir_override
        cfg._log_dir_override = None
        try:
            self.log_dir_edit.setText(str(cfg.log_dir()))
        finally:
            cfg._log_dir_override = prev
