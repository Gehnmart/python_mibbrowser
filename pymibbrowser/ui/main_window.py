"""Main MIB Browser window."""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QModelIndex, Qt, QSortFilterProxyModel, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
    QStatusBar, QTabWidget, QTableView, QTableWidget, QTableWidgetItem,
    QTextBrowser, QToolBar, QTreeView, QVBoxLayout, QWidget,
)

from .. import snmp_ops, workers
from ..config import Agent, AppSettings
from ..i18n import _t, set_language, current_language
from ..mib_loader import MibNode, MibTree
from .mib_tree_model import MibTreeModel, FastMibFilterProxy
from .result_table import ResultTableModel

log = logging.getLogger(__name__)


OPERATIONS = ("Get", "Get Next", "Get Bulk", "Get Subtree", "Walk", "Set")


class MibBrowserWindow(QMainWindow):
    def __init__(self, tree: MibTree, settings: AppSettings) -> None:
        super().__init__()
        self.setWindowTitle(_t("MIB Browser (Python)"))
        self.resize(1180, 780)

        self.tree = tree
        self.settings = settings
        self._active_threads: list = []   # keep refs so worker threads don't GC

        self._build_central()
        self._build_toolbar()
        self._build_menu()
        self._build_status()
        self._expand_to_mib2()
        self._install_agent_hotkeys()
        # Debounced settings save — see _save_settings_soon.
        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.setInterval(500)
        self._settings_save_timer.timeout.connect(self.settings.save)

        # Expose components discovered by Tools windows (they reuse SNMP/MIB).
        self.log_pane: Optional[QPlainTextEdit] = self.log_widget

    # ------------------------------------------------------------------
    # Central widget: MIB tree left | right-side stack with tabs
    # ------------------------------------------------------------------

    def _build_central(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: search + MIB tree + node properties
        left = QSplitter(Qt.Orientation.Vertical)

        tree_pane = QWidget()
        tv = QVBoxLayout(tree_pane)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(2)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        self.mib_search = QLineEdit()
        self.mib_search.setPlaceholderText(_t("Filter MIB tree (substring)…"))
        self.mib_search.setClearButtonEnabled(True)
        filter_row.addWidget(self.mib_search, 1)
        exp_btn = QPushButton("⊞"); exp_btn.setToolTip(_t("Expand all"))
        exp_btn.setFixedWidth(26)
        exp_btn.clicked.connect(lambda: self.mib_view.expandAll())
        filter_row.addWidget(exp_btn)
        col_btn = QPushButton("⊟"); col_btn.setToolTip(_t("Collapse all"))
        col_btn.setFixedWidth(26)
        col_btn.clicked.connect(lambda: self.mib_view.collapseAll())
        filter_row.addWidget(col_btn)
        # Debounce: applying the filter on every keystroke across ~5 000 nodes
        # with recursive filtering freezes the UI. Wait until the user has
        # paused typing for 250 ms before reapplying.
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self._apply_mib_filter_now)
        self.mib_search.textChanged.connect(lambda _: self._filter_timer.start())
        tv.addLayout(filter_row)
        # Second row: "also search in descriptions" checkbox — keeps
        # the primary filter row clean but gives power users a way to
        # find 'temperature'/'cpu'/etc. by hitting the DESCRIPTION.
        from PyQt6.QtWidgets import QCheckBox
        self.search_desc_chk = QCheckBox(_t("Also search in descriptions"))
        self.search_desc_chk.setToolTip(_t(
            "Match the filter text against each node's DESCRIPTION as "
            "well as its name. Slower on big trees."))
        self.search_desc_chk.toggled.connect(self._on_search_desc_toggled)
        tv.addWidget(self.search_desc_chk)

        self.mib_model = MibTreeModel(
            self.tree, single_root=self.settings.single_tree_root)
        self.mib_proxy = FastMibFilterProxy()
        self.mib_proxy.setSourceModel(self.mib_model)

        self.mib_view = QTreeView()
        self.mib_view.setModel(self.mib_proxy)
        self.mib_view.setHeaderHidden(False)
        self.mib_view.setUniformRowHeights(True)
        self.mib_view.setAlternatingRowColors(True)
        self.mib_view.clicked.connect(self._on_tree_clicked)
        self.mib_view.doubleClicked.connect(self._on_tree_double_clicked)
        self.mib_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mib_view.customContextMenuRequested.connect(self._on_tree_context)
        tv.addWidget(self.mib_view, 1)
        left.addWidget(tree_pane)

        self.node_props = QTableWidget(0, 2)
        self.node_props.verticalHeader().setVisible(False)
        self.node_props.horizontalHeader().setVisible(False)
        self.node_props.horizontalHeader().setStretchLastSection(True)
        self.node_props.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.node_props.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.node_props.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.node_props.setAlternatingRowColors(True)
        self.node_props.setWordWrap(True)
        self.node_props.setShowGrid(False)
        left.addWidget(self.node_props)
        left.setSizes([500, 260])

        splitter.addWidget(left)

        # Right: tabs — main _t("Result") tab + dynamic tabs (Tables/Graphs/…)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        # Right-click on a tab → Pin / Close others / Close all.
        bar = self.tabs.tabBar()
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._on_tab_context_menu)

        self.result_tab = self._make_result_tab()
        self.tabs.addTab(self.result_tab, _t("Result"))
        # Result tab can't be closed — and is implicitly pinned.
        self.result_tab.setProperty("pinned", True)
        bar.setTabButton(0, bar.ButtonPosition.RightSide, None)

        splitter.addWidget(self.tabs)
        splitter.setSizes([380, 800])
        self.setCentralWidget(splitter)

    def _make_result_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(2, 2, 2, 2)

        top = QHBoxLayout()
        self.clear_btn = QPushButton(_t("Clear"))
        self.clear_btn.clicked.connect(lambda: self.result_model.clear())
        self.stop_btn = QPushButton(_t("Stop"))
        self.stop_btn.clicked.connect(self._stop_all)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText(_t("Find in table…"))
        self.find_edit.textChanged.connect(self._apply_filter)
        self.save_btn = QPushButton(_t("Save CSV"))
        self.save_btn.clicked.connect(self._save_csv)
        top.addWidget(self.clear_btn)
        top.addWidget(self.stop_btn)
        top.addWidget(QLabel(_t("Find:")))
        top.addWidget(self.find_edit, 1)
        top.addWidget(self.save_btn)
        self.toggle_log_btn = QPushButton(_t("Log"))
        self.toggle_log_btn.setCheckable(True)
        self.toggle_log_btn.setChecked(self.settings.show_log_pane)
        self.toggle_log_btn.setToolTip(_t("Show / hide the log pane"))
        self.toggle_log_btn.toggled.connect(self._toggle_log_pane)
        top.addWidget(self.toggle_log_btn)
        layout.addLayout(top)

        self.result_model = ResultTableModel(self.tree)
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.result_model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self.result_view = QTableView()
        self.result_view.setModel(self.proxy)
        self.result_view.setSortingEnabled(True)
        self.result_view.setAlternatingRowColors(True)
        self.result_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.result_view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self.result_view.horizontalHeader().setStretchLastSection(True)
        self.result_view.setColumnWidth(0, 320)
        self.result_view.doubleClicked.connect(self._on_result_double_clicked)
        self.result_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.result_view.customContextMenuRequested.connect(self._on_result_context)

        self.log_widget = QPlainTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setMaximumBlockCount(2000)
        self.log_widget.setPlaceholderText(_t("Log output"))

        # Vertical splitter — lets user drag the divider to resize the log.
        self.result_split = QSplitter(Qt.Orientation.Vertical)
        self.result_split.addWidget(self.result_view)
        self.result_split.addWidget(self.log_widget)
        self.result_split.setStretchFactor(0, 3)
        self.result_split.setStretchFactor(1, 1)
        self.result_split.setSizes([500, 140])
        self.log_widget.setVisible(self.settings.show_log_pane)
        layout.addWidget(self.result_split, 1)
        return w

    def _toggle_log_pane(self, visible: bool) -> None:
        """Let the user hide the log pane when working with just tables —
        extra vertical space for data. Remembered across sessions."""
        self.log_widget.setVisible(visible)
        self.settings.show_log_pane = visible
        self.settings.save()

    # ------------------------------------------------------------------
    # Toolbar: address, OID, operation, Go
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)

        tb.addWidget(QLabel(_t(" Address: ")))
        # QComboBox (editable) for MRU address history. Populated from
        # settings.saved_agents on startup; new entries added on Go.
        self.addr_edit = QComboBox()
        self.addr_edit.setEditable(True)
        self.addr_edit.setMinimumWidth(200)
        self.addr_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._refresh_agent_combo()
        self.addr_edit.setCurrentText(
            f"{self.settings.current_agent.host}:{self.settings.current_agent.port}")
        self.addr_edit.activated.connect(self._on_saved_agent_chosen)
        tb.addWidget(self.addr_edit)

        # Version & community visible inline — most common "why is it not
        # working?" problems boil down to these two. Hiding them behind an
        # Advanced dialog was a papercut.
        tb.addWidget(QLabel(" " + _t("SNMP") + " "))
        self.version_combo = QComboBox()
        self.version_combo.addItems(("1", "2c", "3"))
        self.version_combo.setCurrentText(self.settings.current_agent.version)
        self.version_combo.currentTextChanged.connect(self._on_version_changed)
        self.version_combo.setToolTip(_t("SNMP version"))
        tb.addWidget(self.version_combo)

        tb.addWidget(QLabel(" " + _t("Community") + ":"))
        self.comm_edit = QLineEdit(self.settings.current_agent.read_community)
        self.comm_edit.setMinimumWidth(90)
        self.comm_edit.setMaximumWidth(140)
        self.comm_edit.editingFinished.connect(self._on_community_changed)
        tb.addWidget(self.comm_edit)

        # ⚙ makes the scope obvious: this button edits the current agent.
        adv = QPushButton("⚙ " + _t("Agent…"))
        adv.setToolTip(_t("Edit the full agent properties (timeout, "
                          "retries, SNMPv3…) for this host."))
        adv.clicked.connect(self._open_agent_dialog)
        tb.addWidget(adv)

        tb.addSeparator()
        tb.addWidget(QLabel(_t(" OID: ")))
        self.oid_edit = QLineEdit()
        self.oid_edit.setMinimumWidth(280)
        self.oid_edit.returnPressed.connect(self._run_operation)
        tb.addWidget(self.oid_edit)
        # History dropdown — separate button so we keep QLineEdit's
        # plain-text API everywhere in the codebase, but give the user
        # one-click access to the last 20 OIDs they actually ran.
        from PyQt6.QtWidgets import QToolButton
        self.oid_history_btn = QToolButton()
        self.oid_history_btn.setText("▾")
        self.oid_history_btn.setToolTip(_t("Recent OIDs"))
        self.oid_history_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.oid_history_menu = QMenu(self.oid_history_btn)
        self.oid_history_btn.setMenu(self.oid_history_menu)
        self.oid_history_menu.aboutToShow.connect(self._rebuild_oid_history_menu)
        tb.addWidget(self.oid_history_btn)

        tb.addSeparator()
        tb.addWidget(QLabel(_t(" Operation: ")))
        self.op_combo = QComboBox()
        self.op_combo.addItems(OPERATIONS)
        self.op_combo.setCurrentText("Get")
        self.op_combo.currentTextChanged.connect(self._sync_go_label)
        tb.addWidget(self.op_combo)

        self.go_btn = QPushButton(_t("Go ▶"))
        self.go_btn.setShortcut(QKeySequence("Ctrl+Return"))
        self.go_btn.clicked.connect(self._run_operation)
        tb.addWidget(self.go_btn)
        self._sync_go_label()

        self.addToolBar(tb)

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        mb = self.menuBar()

        # File — just I/O on *the document*: loading new MIBs, exporting
        # the current result, quitting. MIB cache management moved to &MIB;
        # log file moved to &Tools.
        file_m = mb.addMenu(_t("&File"))
        file_m.addAction(self._action(_t("Load MIB…"), self._load_mibs, "Ctrl+L"))
        file_m.addSeparator()
        file_m.addAction(self._action(_t("Save results as CSV…"), self._save_csv, "Ctrl+S"))
        file_m.addSeparator()
        file_m.addAction(self._action(_t("Exit"), self.close, "Ctrl+Q"))

        edit_m = mb.addMenu(_t("&Edit"))
        # Ctrl+F routes to whichever panel has focus; Ctrl+T is the
        # explicit "find in result" jump regardless of focus.
        edit_m.addAction(self._action(_t("Find"),
                                       self._find_in_focused_panel, "Ctrl+F"))
        edit_m.addAction(self._action(_t("Find in Result"),
                                       self._focus_result_find, "Ctrl+T"))
        edit_m.addAction(self._action(_t("Find in MIB tree"),
                                       self._find_in_tree))
        edit_m.addSeparator()
        edit_m.addAction(self._action(_t("Preferences…"),
                                       self._open_preferences, "Ctrl+,"))

        # MIB — one clear place for "what is loaded into the tree".
        mib_m = mb.addMenu(_t("&MIB"))
        mib_m.addAction(self._action(_t("MIB Modules…"), self._open_mib_modules))
        mib_m.addAction(self._action(_t("Recompile all MIBs…"), self._rebuild_mibs))

        # Operations = SNMP verbs + run/stop. Alternative views (Table
        # View, Graph) moved to the &View menu — those aren't really
        # "operations", they're different ways to display data.
        ops_m = mb.addMenu(_t("&Operations"))
        for op in OPERATIONS:
            a = self._action(op, lambda _=False, o=op: self._run_operation(o))
            ops_m.addAction(a)
        ops_m.addSeparator()
        ops_m.addAction(self._action(_t("Run / Refresh"),
                                     self._refresh_active_tab, "F5"))
        ops_m.addAction(self._action(_t("Stop"),
                                     self._stop_all, "Escape"))
        # No keyboard shortcut on Clear results: the previous 'Delete'
        # binding wiped the whole table when the user was just trying to
        # delete a selected row. The Clear button in the Result toolbar
        # is the discoverable path.
        ops_m.addAction(self._action(_t("Clear results"),
                                     self._clear_results))

        view_m = mb.addMenu(_t("&View"))
        view_m.addAction(self._action(_t("Table View…"), self._open_table_view))
        view_m.addAction(self._action(_t("Graph…"), self._open_graph))

        tools_m = mb.addMenu(_t("&Tools"))
        tools_m.addAction(self._action(_t("Manage agents…"),
                                       self._open_agents_book))
        tools_m.addSeparator()
        tools_m.addAction(self._action(_t("Trap Receiver…"), self._open_trap_receiver))
        tools_m.addAction(self._action(_t("Trap Sender…"), self._open_trap_sender))
        tools_m.addAction(self._action(_t("Agent Simulator…"), self._open_agent_simulator))
        tools_m.addAction(self._action(_t("MIB Editor…"), self._open_mib_editor))
        tools_m.addAction(self._action(_t("Trap daemon log…"), self._open_trapd_console))
        tools_m.addSeparator()
        tools_m.addAction(self._action(_t("Watches…"), self._open_watches))
        tools_m.addAction(self._action(_t("Device Snapshot…"),
                                        self._open_device_snapshot))
        tools_m.addAction(self._action(_t("Port View…"),
                                        self._open_port_view))
        tools_m.addSeparator()
        tools_m.addAction(self._action(_t("Save walk to file…"),
                                        self._open_save_walk))
        tools_m.addAction(self._action(_t("Compare devices…"),
                                        self._open_compare))
        tools_m.addSeparator()
        # Network tools — shell-based ping / traceroute plus a ping-sweep
        # discovery that layers an SNMP GET on top.
        tools_m.addAction(self._action(_t("Ping…"), self._open_ping))
        tools_m.addAction(self._action(_t("Traceroute…"), self._open_traceroute))
        tools_m.addAction(self._action(_t("Network Discovery…"),
                                        self._open_discovery))
        tools_m.addSeparator()
        tools_m.addAction(self._action(_t("Run Script…"), self._run_script))
        tools_m.addAction(self._action(_t("Open log file…"), self._open_log_file))

        self.bookmarks_m = mb.addMenu(_t("&Bookmarks"))
        self._rebuild_bookmarks_menu()

        self.polls_m = mb.addMenu(_t("&Polls"))
        self._rebuild_polls_menu()

        # Help no longer carries the Language submenu — preferences owns it
        # now (a single place to change, reduces churn).
        help_m = mb.addMenu(_t("&Help"))
        help_m.addAction(self._action(_t("Keyboard shortcuts…"),
                                       self._show_shortcuts))
        help_m.addAction(self._action(_t("MIB tree icons…"),
                                       self._show_icon_legend))
        help_m.addSeparator()
        help_m.addAction(self._action(_t("About"), self._about))

    def _action(self, text: str, cb, shortcut: Optional[str] = None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(cb)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        return a

    def _build_status(self) -> None:
        sb = QStatusBar()
        self.status = sb
        self.setStatusBar(sb)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(200)
        self.progress.setTextVisible(False)
        self.progress.hide()
        sb.addPermanentWidget(self.progress)
        self._progress_depth = 0
        # Permanent right-aligned widget showing the current agent —
        # at a glance you see what `Go` will hit, no need to squint at
        # toolbar fields.
        self._agent_status = QLabel("")
        self._agent_status.setStyleSheet("color: #8cf; padding: 0 6px;")
        sb.addPermanentWidget(self._agent_status)
        self._refresh_agent_status()
        sb.showMessage(_t("Ready."))

    def _begin_progress(self, message: str = "",
                        total: int | None = None) -> None:
        """Show a progress indicator in the status bar. Re-entrant — nested
        calls keep the bar visible until the matching ``_end_progress``.
        ``total=N`` = determinate 0..N %, ``None`` = busy/indeterminate."""
        self._progress_depth += 1
        if total is None:
            self.progress.setRange(0, 0)       # busy / indeterminate
        else:
            self.progress.setRange(0, int(total))
            self.progress.setValue(0)
        self.progress.setTextVisible(total is not None)
        self.progress.show()
        if message:
            self.status.showMessage(message)

    def _advance_progress(self, value: int, message: str = "") -> None:
        if self.progress.maximum() > 0:
            self.progress.setValue(value)
        if message:
            self.status.showMessage(message)

    def _end_progress(self, final_message: str = "") -> None:
        self._progress_depth = max(0, self._progress_depth - 1)
        if self._progress_depth == 0:
            self.progress.hide()
            self.progress.setRange(0, 100)
        if final_message:
            self.status.showMessage(final_message)

    # ------------------------------------------------------------------
    # Agent handling
    # ------------------------------------------------------------------

    def _parse_address(self) -> tuple[str, int]:
        s = self.addr_edit.currentText().strip()
        if ":" in s:
            host, port_s = s.rsplit(":", 1)
            return host, int(port_s)
        if "@" in s:
            host, port_s = s.rsplit("@", 1)
            return host, int(port_s)
        return s, 161

    # ---- inline-toolbar edits ---------------------------------------

    def _on_version_changed(self, v: str) -> None:
        self.settings.current_agent.version = v
        self._refresh_agent_status()

    def _on_community_changed(self) -> None:
        self.settings.current_agent.read_community = self.comm_edit.text()
        self._refresh_agent_status()

    def _on_saved_agent_chosen(self, idx: int) -> None:
        saved = self.settings.saved_agents
        if 0 <= idx < len(saved):
            ag = saved[idx]
            self.settings.current_agent = Agent(**vars(ag))
            self.version_combo.setCurrentText(ag.version)
            self.comm_edit.setText(ag.read_community)
            self.addr_edit.setCurrentText(f"{ag.host}:{ag.port}")
            self._refresh_agent_status()

    def _refresh_agent_combo(self) -> None:
        """Rebuild the address dropdown from settings.saved_agents."""
        cur = self.addr_edit.currentText() if hasattr(self, "addr_edit") else ""
        self.addr_edit.blockSignals(True)
        self.addr_edit.clear()
        for ag in self.settings.saved_agents:
            self.addr_edit.addItem(f"{ag.host}:{ag.port}")
        self.addr_edit.setEditText(cur)
        self.addr_edit.blockSignals(False)

    def current_agent(self) -> Agent:
        ag = self.settings.current_agent
        try:
            host, port = self._parse_address()
        except ValueError:
            host, port = ag.host, ag.port
        ag.host = host
        ag.port = port
        ag.version = self.version_combo.currentText()
        ag.read_community = self.comm_edit.text()
        return ag

    def _refresh_agent_status(self) -> None:
        ag = self.current_agent()
        self._agent_status.setText(
            f"{ag.host}:{ag.port} · v{ag.version} · {ag.read_community}")

    def _expand_to_mib2(self) -> None:
        """On startup, auto-expand the path from iso down to mib-2 so the
        user sees populated branches (system, interfaces, …) instead of a
        single 'org' node that they have to hunt through."""
        target = (1, 3, 6, 1, 2, 1)    # iso.org.dod.internet.mgmt.mib-2
        src_idx = self.mib_model.find_index(target)
        if not src_idx.isValid():
            return
        idx = self.mib_proxy.mapFromSource(src_idx)
        cur = idx
        # Expand each ancestor on the path.
        path: list = []
        while cur.isValid():
            path.append(cur)
            cur = cur.parent()
        for p in reversed(path):
            self.mib_view.expand(p)
        self.mib_view.scrollTo(idx)

    def _open_agents_book(self) -> None:
        from .agents_dialog import AgentsDialog
        d = AgentsDialog(self.settings, self)
        if d.exec():
            self._refresh_agent_combo()
            if d.chosen_index is not None \
                    and d.chosen_index < len(self.settings.saved_agents):
                ag = self.settings.saved_agents[d.chosen_index]
                self.settings.current_agent = Agent(**vars(ag))
                self.addr_edit.setCurrentText(f"{ag.host}:{ag.port}")
                self.version_combo.setCurrentText(ag.version)
                self.comm_edit.setText(ag.read_community)
                self.settings.save()
                self._refresh_agent_status()

    def _remember_agent(self, ag: Agent) -> None:
        """Promote the just-used agent to the saved list (MRU, dedup by
        host:port). Debounced — this fires on every SNMP operation so
        coalescing cuts the disk-traffic to once per half-second."""
        key = (ag.host, ag.port)
        existing = [a for a in self.settings.saved_agents
                    if (a.host, a.port) != key]
        self.settings.saved_agents = [Agent(**vars(ag))] + existing[:19]
        self._save_settings_soon()
        self._refresh_agent_combo()

    def _open_agent_dialog(self) -> None:
        from .dialogs import AgentDialog
        d = AgentDialog(self.current_agent(), self)
        if d.exec():
            ag = d.agent
            self.settings.current_agent = ag
            self.addr_edit.setCurrentText(f"{ag.host}:{ag.port}")
            self.version_combo.setCurrentText(ag.version)
            self.comm_edit.setText(ag.read_community)
            self.settings.save()
            self._refresh_agent_status()
            self.status.showMessage(f"Agent updated: {ag.host}:{ag.port} v{ag.version}")

    # ------------------------------------------------------------------
    # MIB tree interactions
    # ------------------------------------------------------------------

    def _node_for_proxy_index(self, proxy_index: QModelIndex):
        src = self.mib_proxy.mapToSource(proxy_index)
        return self.mib_model.node_for_index(src)

    def _on_tree_clicked(self, index: QModelIndex) -> None:
        n = self._node_for_proxy_index(index)
        if n is None:
            return
        self.oid_edit.setText(self._instance_oid(n))
        # Auto-pick the operation that makes sense for this kind of node.
        # A Get on a column's bare OID returns NoSuchInstance — the column
        # has many instances, not one. Walk / Get Next / Get Subtree are the
        # useful verbs for non-scalars.
        self.op_combo.setCurrentText(self._default_op_for(n))
        self._describe(n)

    def _default_op_for(self, n: MibNode) -> str:
        if self._is_notification(n):
            # Notifications aren't pollable. There's no sensible default;
            # pick Get Next so at least the OID can be probed, but the
            # user will normally want the Trap Receiver instead.
            return "Get Next"
        if n.is_scalar:
            return "Get"
        if n.is_column:
            return "Walk"
        if n.is_table or n.is_table_entry:
            return "Get Subtree"
        return "Get Next"

    def _is_notification(self, n: MibNode) -> bool:
        return (n.node_role == "notification"
                or (n.node_type or "").lower() == "notificationtype")

    def _instance_oid(self, n: MibNode) -> str:
        """Return the OID string to put in the toolbar.

        SCALAR objects get .0 appended so a naive GET hits their one instance;
        COLUMNs of a conceptual table get the bare column OID (instances are
        named by the table's index values); anything else (tables, rows,
        groups, OIDs, notifications) also stays bare.

        Uses `node_role` straight from pysmi's MIB metadata — same distinction
        SnmpB makes via libsmi's SMI_NODEKIND_SCALAR vs SMI_NODEKIND_COLUMN.
        """
        base = "." + ".".join(str(p) for p in n.oid)
        return base + ".0" if n.is_scalar else base

    def _on_tree_double_clicked(self, index: QModelIndex) -> None:
        """Double-click used to send SNMP as a side-effect of navigation —
        surprising and occasionally expensive on tables. Now it only
        stages the OID and a sensible default op; the user fires it
        explicitly with Go ▶ / F5 / Enter or via the context menu."""
        n = self._node_for_proxy_index(index)
        if n is None:
            return
        self.oid_edit.setText(self._instance_oid(n))
        self.op_combo.setCurrentText(self._default_op_for(n))
        # Put focus on the Go button so Enter runs it immediately — a
        # one-key follow-up instead of the previous implicit launch.
        self.go_btn.setFocus()

    def _on_tree_context(self, pos) -> None:
        idx = self.mib_view.indexAt(pos)
        n = self._node_for_proxy_index(idx)
        if n is None:
            return
        self.mib_view.setCurrentIndex(idx)
        self.oid_edit.setText(self._instance_oid(n))
        self._describe(n)

        m = QMenu(self)
        m.addAction("Get", lambda: (self.oid_edit.setText(self._instance_oid(n)),
                                    self._run_operation("Get")))
        m.addAction("Get Next", lambda: (self.oid_edit.setText(self._instance_oid(n)),
                                          self._run_operation("Get Next")))
        m.addAction("Walk", lambda: (self.oid_edit.setText(self._instance_oid(n)),
                                      self._run_operation("Walk")))
        if n.is_table or n.is_table_entry:
            m.addAction("Table View", lambda: (self.oid_edit.setText(self._instance_oid(n)),
                                                self._open_table_view()))
        if n.syntax and any(t in n.syntax for t in ("Integer", "Counter", "Gauge",
                                                    "TimeTicks", "Unsigned")):
            m.addAction("Graph", lambda: (self.oid_edit.setText(self._instance_oid(n)),
                                           self._open_graph()))
        m.addSeparator()
        m.addAction(_t("Bookmark…"), lambda: (
            self.oid_edit.setText(self._instance_oid(n)),
            self.op_combo.setCurrentText(self._default_op_for(n)),
            self._bookmark_current_oid()))
        m.addAction(_t("Add to Watches…"), lambda: self._add_to_watches(n))
        m.addSeparator()
        m.addAction(_t("Copy OID"), lambda: QApplication.clipboard().setText(
            "." + ".".join(str(p) for p in n.oid)))
        m.addAction(_t("Copy Name"), lambda: QApplication.clipboard().setText(n.name))
        m.exec(self.mib_view.viewport().mapToGlobal(pos))

    def _describe(self, n: MibNode) -> None:
        """Populate the Name/Value table on the left with the node's metadata."""
        rows: list[tuple[str, str]] = [
            ("Name", n.name),
            ("OID", "." + ".".join(str(p) for p in n.oid)),
        ]
        if n.module:       rows.append(("MIB",        n.module))
        if n.node_type:    rows.append(("Type",       n.node_type))
        if n.syntax:       rows.append(("Syntax",     n.syntax))
        if n.access:       rows.append(("Access",     n.access))
        if n.status:       rows.append(("Status",     n.status))
        if n.units:        rows.append(("Units",      n.units))
        if n.indices:      rows.append(("Indices",    ", ".join(n.indices)))
        if n.enum_values:
            rows.append(("Values",
                         ", ".join(f"{k}={v}"
                                   for k, v in sorted(n.enum_values.items()))))
        if n.description:
            rows.append(("Description", n.description.strip()))

        self.node_props.setRowCount(len(rows))
        bold = QFont()
        bold.setBold(True)
        for r, (k, v) in enumerate(rows):
            name_item = QTableWidgetItem(k)
            name_item.setFont(bold)
            self.node_props.setItem(r, 0, name_item)
            val_item = QTableWidgetItem(v)
            val_item.setToolTip(v)
            self.node_props.setItem(r, 1, val_item)
        self.node_props.resizeRowsToContents()

    def _install_agent_hotkeys(self) -> None:
        """Ctrl+1..9 — switch the toolbar to the top-9 saved agents.
        Index follows the Manage-agents order. No-op if fewer than N
        agents saved."""
        from PyQt6.QtGui import QShortcut, QKeySequence
        for i in range(1, 10):
            sc = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            sc.activated.connect(lambda idx=i - 1: self._switch_saved_agent(idx))

    def _switch_saved_agent(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.settings.saved_agents):
            self.status.showMessage(
                _t("No saved agent at slot {n}").format(n=idx + 1), 2500)
            return
        ag = self.settings.saved_agents[idx]
        # Reuse the dup-aware path from AgentsDialog.Use selected.
        from ..config import Agent
        self.settings.current_agent = Agent(**vars(ag))
        self.addr_edit.setCurrentText(f"{ag.host}:{ag.port}")
        self.version_combo.setCurrentText(ag.version)
        self.comm_edit.setText(ag.read_community)
        self.settings.save()
        self._refresh_agent_status()
        self.status.showMessage(
            _t("Switched to agent #{n}: {host}:{port}").format(
                n=idx + 1, host=ag.host, port=ag.port), 3000)

    def _save_settings_soon(self) -> None:
        """Coalesce frequent setters (toolbar edits, MRU updates) into a
        single disk write ~500 ms after the last change. Callsite that
        needs an immediate flush (close event) still calls .save()."""
        self._settings_save_timer.start()

    def _remember_oid(self, oid_text: str) -> None:
        """MRU list of run-against OIDs. Called once per _run_operation
        — keeps the dropdown sorted newest-first and capped at 20.
        Persisted via the debounced save to avoid a disk round-trip on
        every GET when the user is power-walking a subtree."""
        if not oid_text:
            return
        existing = [o for o in self.settings.recent_oids if o != oid_text]
        self.settings.recent_oids = [oid_text] + existing[:19]
        self._save_settings_soon()

    def _rebuild_oid_history_menu(self) -> None:
        self.oid_history_menu.clear()
        if not self.settings.recent_oids:
            act = self.oid_history_menu.addAction(_t("(no history yet)"))
            act.setEnabled(False)
            return
        for oid in self.settings.recent_oids:
            act = self.oid_history_menu.addAction(oid)
            act.triggered.connect(
                lambda _=False, o=oid: self.oid_edit.setText(o))
        self.oid_history_menu.addSeparator()
        clr = self.oid_history_menu.addAction(_t("Clear history"))
        clr.triggered.connect(self._clear_oid_history)

    def _clear_oid_history(self) -> None:
        self.settings.recent_oids = []
        self.settings.save()

    def _on_search_desc_toggled(self, checked: bool) -> None:
        self.mib_proxy.setSearchDescriptions(checked)
        # Re-apply filter so current needle reruns with the new rule.
        self._apply_mib_filter_now()

    def _find_in_tree(self) -> None:
        """Focus the MIB-tree filter field."""
        self.mib_search.setFocus()
        self.mib_search.selectAll()

    def _sync_go_label(self, *_a) -> None:
        """Echo the chosen op inside Go — so the user can see exactly
        what clicking it (or pressing Ctrl+Return) will fire, even when
        they just alt-tabbed back to the window."""
        op = self.op_combo.currentText() if hasattr(self, "op_combo") else ""
        self.go_btn.setText(f"{_t('Go ▶')} ({op})" if op else _t("Go ▶"))

    def _find_in_focused_panel(self) -> None:
        """Ctrl+F: route to whichever side owns focus. Inside the Result
        pane (or one of the dynamic tabs: Table View / Graph) → result-find;
        anywhere else → the MIB-tree filter. Keeps Ctrl+F feeling local
        instead of hard-coded to the tree."""
        w = QApplication.focusWidget()
        # Walk up to see whether focus lives in the right-hand tabs.
        cur = w
        in_right = False
        while cur is not None:
            if cur is self.tabs:
                in_right = True
                break
            cur = cur.parent() if hasattr(cur, "parent") else None
        if in_right:
            self._focus_result_find()
        else:
            self._find_in_tree()

    def _apply_mib_filter_now(self) -> None:
        """Apply the debounced filter text. We collapse the tree FIRST so
        Qt doesn't spend seconds re-laying-out 5 000 currently-visible rows
        while the filter re-evaluates; then filter, then expand matches."""
        text = self.mib_search.text()
        self.mib_view.setUpdatesEnabled(False)
        try:
            self.mib_view.collapseAll()
            self.mib_proxy.setFilterFixedString(text)
            if text:
                self.mib_view.expandAll()
        finally:
            self.mib_view.setUpdatesEnabled(True)

    def _focus_result_find(self) -> None:
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    # ------------------------------------------------------------------
    # Running SNMP operations
    # ------------------------------------------------------------------

    def _refresh_active_tab(self) -> None:
        """F5 — refresh the *visible* tab. On Result, repeat the current
        SNMP op; on Table View, re-walk the table; on Graph, force a poll.
        Previously F5 always re-ran the operation into Result even when
        the user was looking at a Table or Graph tab — misleading."""
        w = self.tabs.currentWidget()
        if w is self.result_tab:
            self._run_operation()
            return
        # Duck-type against the tab's refresh hook so we don't have to
        # import TableViewTab / GraphTab here just to isinstance-check.
        for attr in ("_refresh", "_poll"):
            fn = getattr(w, attr, None)
            if callable(fn):
                fn()
                return
        # Fall back to running the op against Result — better than silence.
        self._run_operation()

    def _run_operation(self, op: Optional[str] = None) -> None:
        if isinstance(op, bool) or op is None:
            op = self.op_combo.currentText()
        oid_text = self.oid_edit.text().strip()
        if not oid_text:
            QMessageBox.warning(self, "OID", "Enter an OID (name or numeric).")
            return
        resolved = self.tree.resolve_name(oid_text)
        if resolved is None:
            QMessageBox.warning(self, "OID", f"Cannot resolve {oid_text!r}.")
            return
        # Broad-walk guardrail: WALK/Get Subtree at the top of the tree
        # (e.g. .1 or .1.3.6.1) can flood for minutes. Warn with a rough
        # scope hint so the user doesn't accidentally ask for everything.
        if op in ("Walk", "Get Subtree") and len(resolved) <= 6:
            node = self.tree.lookup_oid(resolved)
            approx = 0
            if node is not None:
                stack = [node]
                while stack and approx < 10000:
                    cur = stack.pop()
                    approx += 1
                    stack.extend(cur.children)
            scope = oid_text if oid_text.startswith(".") \
                    else "." + ".".join(str(x) for x in resolved)
            btn = QMessageBox.question(
                self, op,
                _t("{op} on a broad subtree '{scope}' — this may return "
                   "thousands of varbinds and take minutes.\n\n"
                   "Known MIB nodes below it: ~{approx}{plus}.\n\n"
                   "Run anyway?").format(
                    op=op, scope=scope, approx=approx,
                    plus="+" if approx >= 10000 else ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if btn != QMessageBox.StandardButton.Yes:
                return
        agent = self.current_agent()
        self._remember_agent(agent)
        self._remember_oid(oid_text)
        source = f"{agent.host}:{agent.port}"
        self._log(f">>> {op} {oid_text}  ({'.' + '.'.join(map(str,resolved))})  @{source}")
        # Switch to the Result tab so the newly-arriving rows are visible —
        # without this, the user might be looking at a Table: … tab and not
        # notice the result landing.
        self.tabs.setCurrentIndex(0)

        if op == "Get":
            self._start_worker(snmp_ops.op_get, agent, [resolved], source=source, op=op)
        elif op == "Get Next":
            self._start_worker(snmp_ops.op_next, agent, [resolved], source=source, op=op)
        elif op == "Get Bulk":
            self._start_worker(snmp_ops.op_bulk, agent, [resolved], source=source, op=op)
        elif op in ("Walk", "Get Subtree"):
            self._start_worker(snmp_ops.op_walk, agent, resolved, source=source, op=op,
                               is_walk=True)
        elif op == "Set":
            self._do_set(agent, resolved, source)

    def _start_worker(self, fn, *args, source: str, op: str, is_walk: bool = False) -> None:
        workers.prune_threads(self._active_threads)
        def on_progress(vb):
            self.result_model.add(vb, source)
            if is_walk:
                self._advance_progress(
                    0, f"{op}: {self.result_model.row_count()} varbinds…")
        def on_finished(vbs):
            if not is_walk:
                self.result_model.extend(vbs, source)
            self._log(f"<<< {op} finished: {len(vbs)} varbind(s)")
            if op == "Get" and vbs and any(
                    v.type_name in ("NoSuchInstance", "NoSuchObject")
                    for v in vbs):
                # Tailor the hint to what this OID actually is — the
                # "use Walk / Get Subtree" tip makes no sense on a
                # notification, for example.
                resolved_node = self.tree.lookup_oid(vbs[0].oid) if vbs else None
                if resolved_node is not None and self._is_notification(resolved_node):
                    hint = _t(
                        "hint: this OID is a notification (TRAP/INFORM). "
                        "It isn't pollable with GET — it's sent by the "
                        "agent. Use Tools → Trap Receiver to listen for it.")
                elif resolved_node is not None and (
                        resolved_node.is_table or resolved_node.is_table_entry):
                    hint = _t(
                        "hint: this OID is a table/row. Use Get Subtree "
                        "or open Table View to see rows.")
                elif resolved_node is not None and resolved_node.is_column:
                    hint = _t(
                        "hint: this OID is a table column. Use Walk or "
                        "Get Next to enumerate instances.")
                else:
                    hint = _t(
                        "hint: that OID has no scalar instance. For a column, "
                        "use Walk or Get Next; for a table, use Get Subtree or "
                        "open Table View.")
                self._log("    " + hint, level="hint")
            self._end_progress(f"{op}: {len(vbs)} result(s) from {source}")
        def on_failed(msg):
            self._log(f"!!! {op} failed: {msg}")
            self._end_progress(f"{op} failed: {msg}")
            QMessageBox.warning(self, op, msg)
        self._begin_progress(f"{op} in progress…")
        t, _w = workers.run_op(
            self, fn, on_finished, on_failed,
            on_progress if is_walk else None, *args,
        )
        self._active_threads.append(t)

    def _do_set(self, agent: Agent, oid: tuple[int, ...], source: str) -> None:
        from .dialogs import SetDialog
        node = self.tree.lookup_oid(oid)
        d = SetDialog("." + ".".join(str(p) for p in oid), node, self)
        if not d.exec():
            return
        type_tag, text = d.result()
        try:
            value = snmp_ops.build_set_value(type_tag, text)
        except Exception as exc:
            QMessageBox.warning(self, "Set", f"Invalid value: {exc}")
            return
        self._start_worker(snmp_ops.op_set, agent, [(oid, value)],
                           source=source, op="Set")

    def _clear_results(self) -> None:
        self.result_model.clear()
        self.status.showMessage(_t("Ready."))

    def _stop_all(self) -> None:
        # pysnmp v7 doesn't expose cancellation cleanly for us; workers finish on
        # their own timeouts. We just disassociate UI so no more progress rows
        # land in the table.
        self._log("Stop requested — pending SNMP calls will finish on timeout.")

    # ------------------------------------------------------------------
    # Result table interactions
    # ------------------------------------------------------------------

    def _apply_filter(self, s: str) -> None:
        self.proxy.setFilterFixedString(s)

    def _on_result_double_clicked(self, index: QModelIndex) -> None:
        # Toggle string/hex display? For now just copy value.
        src = self.proxy.mapToSource(index)
        if not src.isValid():
            return
        vb, _ = self.result_model.get(src.row())
        self.oid_edit.setText(self.tree.format_oid(vb.oid))

    def _on_result_context(self, pos) -> None:
        idx = self.result_view.indexAt(pos)
        src = self.proxy.mapToSource(idx)
        if not src.isValid():
            return
        vb, _ = self.result_model.get(src.row())
        m = QMenu(self)
        m.addAction("Get this", lambda: (self.oid_edit.setText(self.tree.format_oid(vb.oid)),
                                          self._run_operation("Get")))
        m.addAction("Walk this", lambda: (self.oid_edit.setText(self.tree.format_oid(vb.oid)),
                                           self._run_operation("Walk")))
        m.addAction("Graph", lambda: (self.oid_edit.setText(self.tree.format_oid(vb.oid)),
                                       self._open_graph()))
        m.addAction("Table View", lambda: (self.oid_edit.setText(self.tree.format_oid(vb.oid)),
                                            self._open_table_view()))
        m.addSeparator()
        m.addAction("Copy OID", lambda: QApplication.clipboard().setText(
            "." + ".".join(str(p) for p in vb.oid)))
        m.addAction("Copy value", lambda: QApplication.clipboard().setText(vb.display_value))
        m.exec(self.result_view.viewport().mapToGlobal(pos))

    def _save_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save results", "results.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Name/OID", "NumericOID", "Value", "Type", "Source"])
            for i in range(self.result_model.row_count()):
                vb, src = self.result_model.get(i)
                w.writerow([self.tree.format_oid(vb.oid),
                            "." + ".".join(str(p) for p in vb.oid),
                            vb.display_value, vb.type_name, src])
        self.status.showMessage(f"Saved {self.result_model.row_count()} rows to {path}", 5000)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _close_tab(self, idx: int) -> None:
        if idx == 0:
            return
        w = self.tabs.widget(idx)
        # Don't close a pinned tab even if its × was somehow clicked.
        if w.property("pinned"):
            return
        self.tabs.removeTab(idx)
        w.deleteLater()

    def _on_tab_context_menu(self, pos) -> None:
        """Right-click on a tab bar → Pin / Unpin / Close others / Close all."""
        bar = self.tabs.tabBar()
        idx = bar.tabAt(pos)
        if idx < 0:
            return
        w = self.tabs.widget(idx)
        pinned = bool(w.property("pinned"))
        is_result = (idx == 0)

        m = QMenu(self)
        if not is_result:
            if pinned:
                # Pinned tabs can't be closed by themselves — user has to
                # unpin first (by choosing Unpin, or Unpin and close).
                # Keeps the mental model consistent: × is hidden on a
                # pinned tab, and so is the Close action.
                m.addAction(_t("Unpin tab"),
                            lambda: self._set_tab_pinned(idx, False))
                m.addAction(_t("Unpin and close"),
                            lambda: self._unpin_and_close(idx))
            else:
                m.addAction(_t("Pin tab"),
                            lambda: self._set_tab_pinned(idx, True))
                m.addSeparator()
                m.addAction(_t("Close"), lambda: self._close_tab(idx))
        m.addAction(_t("Close other tabs"),
                    lambda: self._close_other_tabs(idx))
        m.addAction(_t("Close all tabs"), self._close_all_tabs)
        m.exec(bar.mapToGlobal(pos))

    def _unpin_and_close(self, idx: int) -> None:
        self._set_tab_pinned(idx, False)
        self._close_tab(idx)

    def _set_tab_pinned(self, idx: int, pinned: bool) -> None:
        """Mark a tab as pinned — hides its × button and protects it from
        'Close others' / 'Close all'. Also drags it to the left of the
        tab row so pinned tabs cluster together, matching browser UX."""
        w = self.tabs.widget(idx)
        w.setProperty("pinned", pinned)
        bar = self.tabs.tabBar()
        if pinned:
            bar.setTabButton(idx, bar.ButtonPosition.RightSide, None)
            # Prefix with a pin glyph so it's visually obvious.
            txt = self.tabs.tabText(idx)
            if not txt.startswith("📌 "):
                self.tabs.setTabText(idx, "📌 " + txt)
            # Move just after the last already-pinned tab.
            target = 1  # Result is 0 and always first
            for i in range(1, self.tabs.count()):
                if i == idx:
                    continue
                if self.tabs.widget(i).property("pinned"):
                    target = i + 1
            if target != idx:
                self.tabs.tabBar().moveTab(idx, target)
                self.tabs.setCurrentIndex(target)
        else:
            # Restore × button by re-enabling closable-ness per-tab (Qt only
            # exposes a global flag, so we just set the button back).
            from PyQt6.QtWidgets import QToolButton, QStyle
            btn = QToolButton()
            btn.setIcon(self.style().standardIcon(
                QStyle.StandardPixmap.SP_TitleBarCloseButton))
            btn.setAutoRaise(True)
            btn.clicked.connect(
                lambda _=False, b=btn: self._close_tab(self.tabs.indexOf(
                    b.parent().parent())))
            bar.setTabButton(idx, bar.ButtonPosition.RightSide, btn)
            txt = self.tabs.tabText(idx)
            if txt.startswith("📌 "):
                self.tabs.setTabText(idx, txt[2:])

    def _close_other_tabs(self, keep_idx: int) -> None:
        """Close everything except the Result tab, pinned tabs, and keep_idx."""
        for i in range(self.tabs.count() - 1, 0, -1):
            if i == keep_idx:
                continue
            if self.tabs.widget(i).property("pinned"):
                continue
            self._close_tab(i)

    def _close_all_tabs(self) -> None:
        """Close everything closable (Result tab + pinned tabs stay)."""
        for i in range(self.tabs.count() - 1, 0, -1):
            if self.tabs.widget(i).property("pinned"):
                continue
            self._close_tab(i)

    def _open_table_view(self) -> None:
        from .table_view import TableViewTab
        oid_text = self.oid_edit.text().strip()
        if not oid_text:
            QMessageBox.warning(self, "Table", "Select a table OID first.")
            return
        resolved = self.tree.resolve_name(oid_text)
        if resolved is None:
            return
        agent = self.current_agent()
        tab = TableViewTab(self, self.tree, agent, resolved)
        idx = self.tabs.addTab(tab, f"Table: {oid_text}")
        self.tabs.setCurrentIndex(idx)

    def _open_graph(self, rate: bool = False) -> None:
        """Open a Graph tab on the currently-staged OID.

        `rate` — if True, pre-enable the 'Rate (delta)' toggle so a
        cumulative SNMP counter (ifInOctets, sysUpTime, …) is plotted as
        a derivative instead of a monotone line going only up. Port View
        calls with rate=True."""
        from .graph_window import GraphTab
        oid_text = self.oid_edit.text().strip()
        if not oid_text:
            QMessageBox.warning(self, "Graph", "Select a numeric OID.")
            return
        resolved = self.tree.resolve_name(oid_text)
        if resolved is None:
            return
        agent = self.current_agent()
        tab = GraphTab(self, agent, resolved, oid_text, tree=self.tree)
        if rate:
            tab.rate_chk.setChecked(True)
        idx = self.tabs.addTab(tab, f"Graph: {oid_text}")
        self.tabs.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _open_trap_receiver(self) -> None:
        from .trap_window import TrapReceiverWindow
        w = TrapReceiverWindow(self.tree, self.settings, self)
        w.show()
        # Keep ref
        self._trap_win = w

    def _open_trap_sender(self) -> None:
        from .tool_dialogs import TrapSenderDialog
        d = TrapSenderDialog(self.tree, self.current_agent(), self)
        d.show()
        self._trap_sender_win = d

    def _open_agent_simulator(self) -> None:
        from .tool_dialogs import AgentSimulatorDialog
        d = AgentSimulatorDialog(self.tree, self)
        d.show()
        self._sim_win = d

    def _open_mib_editor(self) -> None:
        from .tool_dialogs import MibEditorDialog
        d = MibEditorDialog(self)
        d.show()
        self._mib_edit_win = d

    def _open_trapd_console(self) -> None:
        from .tool_dialogs import TrapdConsoleDialog
        d = TrapdConsoleDialog(self.tree, self.settings, self)
        d.show()
        self._trapd_win = d

    def _run_script(self) -> None:
        """Open the Script Runner — editor + Reference tab + examples.
        Replaces the bare file-picker that left the user with no idea
        what format a script should take."""
        from .script_dialog import ScriptDialog
        d = ScriptDialog(self.current_agent(), self.tree, self)
        d.exec()

    def _open_save_walk(self) -> None:
        from .save_walk_dialog import SaveWalkDialog
        SaveWalkDialog(self.current_agent(), tree=self.tree, parent=self).exec()

    def _open_compare(self) -> None:
        from .compare_dialog import CompareDialog
        CompareDialog(self.settings, tree=self.tree, parent=self).exec()

    def _open_ping(self) -> None:
        from .network_tools import PingDialog
        host = self.current_agent().host
        PingDialog(default_host=host, parent=self).exec()

    def _open_traceroute(self) -> None:
        from .network_tools import TracerouteDialog
        host = self.current_agent().host
        TracerouteDialog(default_host=host, parent=self).exec()

    def _open_discovery(self) -> None:
        from .network_tools import DiscoveryDialog
        d = DiscoveryDialog(self.current_agent, parent=self,
                             settings=self.settings)
        d.exec()
        # User may have added agents via the 'Add selected' button —
        # refresh the Address combo so they show up in the dropdown.
        self._refresh_agent_combo()

    # ------------------------------------------------------------------
    # MIB loading
    # ------------------------------------------------------------------

    def _load_mibs(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, _t("Load MIB…"), "",
            "MIB (*.mib *.my *.txt *.smi);;All (*)")
        if not files:
            return
        from .. import config
        from .mib_load_dialog import MibLoadDialog

        src_paths = [Path(f) for f in files]
        dest = config.default_mibs_src()
        dest.mkdir(parents=True, exist_ok=True)

        staged: list[str] = []
        for sp in src_paths:
            target = dest / sp.name
            if target.resolve() != sp.resolve():
                target.write_bytes(sp.read_bytes())
            mod = (sp.stem if sp.name.lower().endswith(
                ("mib", ".my", ".txt", ".smi")) else sp.name).upper()
            staged.append(mod)

        # Auto-detect the user's MIB repo root — we walk up ≤3 levels looking
        # for a directory that holds ≥5 MIB files recursively. Vendors
        # distribute MIBs in subdirs (eltexLtd/, wlc/, cpe/, …) and having
        # to click "Add source dir" for every dep is annoying. pysmi's
        # FileReader with recursive=True handles it once we give it the root.
        extra_dirs = self._guess_mib_repo_dirs(src_paths)

        dlg = MibLoadDialog(staged, self, extra_dirs=extra_dirs)
        dlg.exec()
        self._reload_mib_tree()

    @staticmethod
    def _guess_mib_repo_dirs(files: list[Path]) -> list[Path]:
        """Derive search directories from the user's file picks.

        Strategy: include the immediate parent of each picked file, then
        look one level up — if THAT grandparent looks like a MIB repo root
        (several of its direct subdirs each contain MIB files), add each
        of those subdirs as an INDEPENDENT search root.

        Independent per-subdir roots matter because vendor repos often ship
        multiple versions of the same module in parallel subdirs
        (eltexLtd/, mes5448/, mes24xx/…); if we gave pysmi one recursive
        reader it would pick the first match, which may be an older copy
        missing symbols the caller needs."""
        out: list[Path] = []
        seen: set[Path] = set()

        def add(p: Path) -> None:
            r = p.resolve()
            if r not in seen:
                seen.add(r)
                out.append(p)

        for f in files:
            parent = f.parent
            add(parent)
            grand = parent.parent
            if not grand.exists() or str(grand) in ("/", ""):
                continue
            # Count sibling dirs under grandparent that contain MIBs.
            mibby_subdirs = []
            try:
                for sub in grand.iterdir():
                    if not sub.is_dir():
                        continue
                    has_mibs = False
                    for ext in ("*.mib", "*.my", "*.smi"):
                        try:
                            if next(sub.glob(ext), None) is not None \
                               or next(sub.rglob(ext), None) is not None:
                                has_mibs = True
                                break
                        except (OSError, PermissionError):
                            pass
                    if has_mibs:
                        mibby_subdirs.append(sub)
            except (OSError, PermissionError):
                continue
            if len(mibby_subdirs) >= 2:
                # Looks like a vendor repo with multiple product lines.
                # Add each subdir individually and sorted, so lookups are
                # deterministic. The picked file's own dir is already
                # first (added above) so its symbols win ties; the rest
                # fill in missing deps.
                for sub in sorted(mibby_subdirs):
                    add(sub)
        return out

    # ---- Bookmarks --------------------------------------------------

    def _rebuild_bookmarks_menu(self) -> None:
        self.bookmarks_m.clear()
        self.bookmarks_m.addAction(self._action(
            _t("Bookmark current OID…"),
            self._bookmark_current_oid, "Ctrl+D"))
        self.bookmarks_m.addAction(self._action(
            _t("Edit bookmarks…"), self._edit_bookmarks))
        if self.settings.bookmarks:
            self.bookmarks_m.addSeparator()
        if self.settings.bookmarks:
            hint = QAction(_t("Tip: Shift+click to load without running"), self)
            hint.setEnabled(False)
            self.bookmarks_m.addAction(hint)
            self.bookmarks_m.addSeparator()
        for bm in self.settings.bookmarks:
            # Prefix shows what clicking will do: [Op], [Table], [Graph].
            view = bm.get("view", "op")
            if view == "table":
                badge = "[T]"
            elif view == "graph":
                badge = "[G]"
            else:
                badge = f"[{bm.get('operation', 'Get')}]"
            text = f"{badge}  {bm.get('name', '?')} — {bm.get('oid','')}"
            act = QAction(text, self)
            act.triggered.connect(lambda _=False, b=bm: self._run_bookmark(b))
            self.bookmarks_m.addAction(act)

    # Bookmark Operation combo items. View-type bookmarks are just another
    # entry in the same list — matches iReasoning's single-dropdown UX
    # rather than the old radio-buttons-plus-combo split.
    _BOOKMARK_OPS: tuple[tuple[str, str, str], ...] = (
        ("Get",         "op",    "Get"),
        ("Get Next",    "op",    "Get Next"),
        ("Get Bulk",    "op",    "Get Bulk"),
        ("Get Subtree", "op",    "Get Subtree"),
        ("Walk",        "op",    "Walk"),
        ("Set",         "op",    "Set"),
        ("Table View",  "table", ""),
        ("Graph",       "graph", ""),
    )

    def _show_bookmark_editor(self, seed: Optional[dict]) -> Optional[dict]:
        """Shared editor: pre-fill with `seed` (None = new from toolbar).
        Returns the saved dict or None on cancel.

        The dialog mirrors iReasoning's Bookmark OID: Name / OID / one
        Operation combo. 'Table View' and 'Graph' are just entries in
        that combo — simpler than the previous radio-vs-combo hybrid."""
        from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout,
                                     QLineEdit, QVBoxLayout, QComboBox)
        if seed is None:
            oid = self.oid_edit.text().strip()
            if not oid:
                QMessageBox.information(self, _t("Bookmarks"),
                                        _t("No OID in the toolbar to bookmark."))
                return None
            seed = {"name": oid, "oid": oid, "view": "op",
                    "operation": self.op_combo.currentText()}

        d = QDialog(self); d.setWindowTitle(_t("Bookmark OID"))
        d.setMinimumWidth(420)
        v = QVBoxLayout(d)
        form = QFormLayout()
        name_edit = QLineEdit(seed.get("name", ""))
        oid_edit = QLineEdit(seed.get("oid", ""))
        op_combo = QComboBox()
        for label, _view, _op in self._BOOKMARK_OPS:
            op_combo.addItem(label)
        # Pre-select the entry matching seed's view/operation.
        seed_view = seed.get("view", "op")
        seed_op = seed.get("operation") or "Get"
        idx = 0
        for i, (label, view, op) in enumerate(self._BOOKMARK_OPS):
            if view == seed_view and (view != "op" or op == seed_op):
                idx = i
                break
        op_combo.setCurrentIndex(idx)

        form.addRow(_t("Name") + ":", name_edit)
        form.addRow(_t("OID") + ":", oid_edit)
        form.addRow(_t("Operation") + ":", op_combo)
        v.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept); bb.rejected.connect(d.reject)
        v.addWidget(bb)
        if not d.exec():
            return None

        name = name_edit.text().strip()
        oid_text = oid_edit.text().strip()
        if not name or not oid_text:
            return None
        _label, new_view, new_op = self._BOOKMARK_OPS[op_combo.currentIndex()]
        return {"name": name, "oid": oid_text,
                "operation": new_op, "view": new_view}

    def _bookmark_current_oid(self) -> None:
        result = self._show_bookmark_editor(None)
        if result is None:
            return
        self.settings.bookmarks.append(result)
        self.settings.save()
        self._rebuild_bookmarks_menu()

    def _run_bookmark(self, bm: dict) -> None:
        """Replay a bookmark — routes to Table View / Graph / plain op.

        Shift held at click-time = preview only: load the OID / op into
        the toolbar but don't fire. Lets the user inspect a bookmark
        (e.g. reconfirm the target before running something heavy)
        without immediately triggering network traffic."""
        oid = bm.get("oid", "")
        self.oid_edit.setText(oid)
        view = bm.get("view", "op")
        op = bm.get("operation") or "Get"
        if view == "op":
            self.op_combo.setCurrentText(op)
        preview = bool(QApplication.keyboardModifiers()
                       & Qt.KeyboardModifier.ShiftModifier)
        if preview:
            self.status.showMessage(
                _t("Bookmark loaded — press Go ▶ to run."), 4000)
            return
        if view == "table":
            self._open_table_view()
        elif view == "graph":
            self._open_graph()
        else:
            self._run_operation(op)

    # ---- Polls ------------------------------------------------------

    def _rebuild_polls_menu(self) -> None:
        self.polls_m.clear()
        self.polls_m.addAction(self._action(
            _t("Create Poll…"), self._create_poll))
        self.polls_m.addAction(self._action(
            _t("Manage Polls…"), self._manage_polls))
        if self.settings.polls:
            self.polls_m.addSeparator()
        for p in self.settings.polls:
            act = QAction(f"▶ {p.name}  ({p.interval_s}s)", self)
            act.triggered.connect(lambda _=False, q=p: self._open_poll_tab(q))
            self.polls_m.addAction(act)

    def _create_poll(self) -> None:
        from .polls_dialog import PollDialog
        d = PollDialog(None, self.settings, tree=self.tree, parent=self)
        if not d.exec():
            return
        self.settings.polls.append(d.result_poll)
        self.settings.save()
        self._rebuild_polls_menu()
        self._open_poll_tab(d.result_poll)

    def _manage_polls(self) -> None:
        """Small list dialog: Edit / Run / Delete saved polls."""
        from PyQt6.QtWidgets import (QDialog, QListWidget, QListWidgetItem,
                                     QPushButton, QHBoxLayout, QVBoxLayout)
        d = QDialog(self); d.setWindowTitle(_t("Manage Polls"))
        d.resize(520, 380)
        v = QVBoxLayout(d)
        lst = QListWidget()

        def _refill(select: int = 0) -> None:
            lst.clear()
            for p in self.settings.polls:
                QListWidgetItem(
                    f"{p.name}  — {len(p.agents)} agent(s) × "
                    f"{len(p.variables)} var(s), every {p.interval_s}s",
                    lst)
            if self.settings.polls:
                lst.setCurrentRow(max(0, min(select, lst.count() - 1)))
        _refill()

        def _idx() -> int:
            r = lst.currentRow()
            return r if 0 <= r < len(self.settings.polls) else -1

        def _edit():
            r = _idx()
            if r < 0:
                return
            from .polls_dialog import PollDialog
            dlg = PollDialog(self.settings.polls[r], self.settings,
                             tree=self.tree, parent=d)
            if dlg.exec():
                self.settings.polls[r] = dlg.result_poll
                self.settings.save()
                _refill(select=r)

        def _run():
            r = _idx()
            if r < 0:
                return
            p = self.settings.polls[r]
            d.accept()
            self._open_poll_tab(p)

        def _delete():
            r = _idx()
            if r < 0:
                return
            del self.settings.polls[r]
            self.settings.save()
            _refill(select=max(0, r - 1))

        lst.itemDoubleClicked.connect(lambda _i: _edit())
        v.addWidget(lst, 1)

        hb = QHBoxLayout()
        edit_b = QPushButton(_t("Edit")); edit_b.clicked.connect(_edit)
        del_b = QPushButton(_t("Delete")); del_b.clicked.connect(_delete)
        run_b = QPushButton(_t("Run")); run_b.setDefault(True); run_b.clicked.connect(_run)
        close_b = QPushButton(_t("Close")); close_b.clicked.connect(d.accept)
        for b in (edit_b, del_b, run_b):
            hb.addWidget(b)
        hb.addStretch()
        hb.addWidget(close_b)
        v.addLayout(hb)
        d.exec()
        self._rebuild_polls_menu()

    def _open_poll_tab(self, poll) -> None:
        from .poll_tab import PollTab
        tab = PollTab(self, poll, self.settings, tree=self.tree)
        idx = self.tabs.addTab(tab, f"Poll: {poll.name}")
        self.tabs.setCurrentIndex(idx)

    # ---- Watches ----------------------------------------------------

    def _open_watches(self) -> None:
        """Single shared Watches tab — focus it if already open, else
        create. Avoids duplicate timers polling the same watch list."""
        from .watches_tab import WatchesTab
        for i in range(self.tabs.count()):
            if isinstance(self.tabs.widget(i), WatchesTab):
                self.tabs.setCurrentIndex(i)
                return
        tab = WatchesTab(self, self.settings, self.current_agent,
                         tree=self.tree)
        idx = self.tabs.addTab(tab, _t("Watches"))
        self.tabs.setCurrentIndex(idx)

    def _open_device_snapshot(self) -> None:
        from .device_snapshot_tab import DeviceSnapshotTab
        tab = DeviceSnapshotTab(self, self.settings, self.current_agent,
                                 tree=self.tree)
        agent = self.current_agent()
        idx = self.tabs.addTab(tab, f"Snapshot: {agent.host}")
        self.tabs.setCurrentIndex(idx)

    def _open_port_view(self) -> None:
        from .port_view_tab import PortViewTab
        tab = PortViewTab(self, self.settings, self.current_agent,
                          tree=self.tree)
        agent = self.current_agent()
        idx = self.tabs.addTab(tab, f"Ports: {agent.host}")
        self.tabs.setCurrentIndex(idx)

    def _add_to_watches(self, n) -> None:
        """Entrance point from tree / result-row context menu."""
        from .watches_tab import AddWatchDialog
        from ..config import WatchDefinition
        seed = WatchDefinition(
            name=n.name,
            oid="." + ".".join(str(x) for x in n.oid),
            operation=self._default_op_for(n),
        )
        d = AddWatchDialog(seed, tree=self.tree, parent=self)
        if not d.exec():
            return
        self.settings.watches.append(d.result_watch)
        self.settings.save()
        # Open/focus the tab so the user sees what they added.
        self._open_watches()

    def _edit_bookmarks(self) -> None:
        """Manage Bookmarks — matches iReasoning layout: Name / OID /
        Operation columns with Edit / Delete / Go / Close buttons on
        the right. Double-click / Enter = Edit; Go fires the bookmark
        without leaving the dialog."""
        from PyQt6.QtWidgets import (QDialog, QTableWidget, QTableWidgetItem,
                                     QPushButton, QHBoxLayout, QVBoxLayout,
                                     QAbstractItemView)
        d = QDialog(self); d.setWindowTitle(_t("Manage Bookmarks"))
        d.resize(640, 420)
        h = QHBoxLayout(d)

        tbl = QTableWidget(0, 3)
        tbl.setHorizontalHeaderLabels([_t("Name"), _t("OID"), _t("Operation")])
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tbl.horizontalHeader().setStretchLastSection(False)

        def _op_label(bm: dict) -> str:
            view = bm.get("view", "op")
            if view == "table":
                return _t("Table View")
            if view == "graph":
                return _t("Graph")
            return bm.get("operation") or "Get"

        def _refill(select: int = 0) -> None:
            tbl.setRowCount(len(self.settings.bookmarks))
            for r, bm in enumerate(self.settings.bookmarks):
                tbl.setItem(r, 0, QTableWidgetItem(bm.get("name", "")))
                tbl.setItem(r, 1, QTableWidgetItem(bm.get("oid", "")))
                tbl.setItem(r, 2, QTableWidgetItem(_op_label(bm)))
            tbl.resizeColumnsToContents()
            tbl.horizontalHeader().setStretchLastSection(True)
            if self.settings.bookmarks:
                tbl.selectRow(max(0, min(select, tbl.rowCount() - 1)))

        def _selected() -> int:
            r = tbl.currentRow()
            return r if 0 <= r < len(self.settings.bookmarks) else -1

        def _edit_current():
            r = _selected()
            if r < 0:
                return
            seed = self.settings.bookmarks[r]
            updated = self._show_bookmark_editor(seed)
            if updated is None:
                return
            self.settings.bookmarks[r] = updated
            self.settings.save()
            _refill(select=r)

        def _delete_current():
            r = _selected()
            if r < 0:
                return
            del self.settings.bookmarks[r]
            self.settings.save()
            _refill(select=max(0, r - 1))

        def _go_current():
            r = _selected()
            if r < 0:
                return
            bm = self.settings.bookmarks[r]
            d.accept()
            self._run_bookmark(bm)

        tbl.doubleClicked.connect(lambda _i: _edit_current())
        h.addWidget(tbl, 1)

        # Right-hand button column, iReasoning style.
        btn_col = QVBoxLayout()
        edit_btn = QPushButton(_t("Edit"))
        edit_btn.clicked.connect(_edit_current)
        del_btn = QPushButton(_t("Delete"))
        del_btn.clicked.connect(_delete_current)
        go_btn = QPushButton(_t("Go"))
        go_btn.setDefault(True)
        go_btn.clicked.connect(_go_current)
        close_btn = QPushButton(_t("Close"))
        close_btn.clicked.connect(d.accept)
        for b in (edit_btn, del_btn, go_btn):
            btn_col.addWidget(b)
        btn_col.addStretch()
        btn_col.addWidget(close_btn)
        h.addLayout(btn_col)

        _refill()
        d.exec()
        self._rebuild_bookmarks_menu()

    def _show_icon_legend(self) -> None:
        """Explain the glyphs in the MIB tree — same mapping that
        mib_tree_model._icon_for uses. Keeps this out-of-reach legend
        discoverable from Help."""
        rows = [
            ("📁", _t("Organisational group (container, no value)")),
            ("📋", _t("Conceptual table (SMIv2 TABLE)")),
            ("📑", _t("Table row (entry — defines per-instance columns)")),
            ("📝", _t("Writable scalar (read-write / read-create)")),
            ("📖", _t("Read-only scalar")),
            ("🔔", _t("Notification / TRAP / INFORM")),
            ("•",  _t("Leaf object without a specific role")),
        ]
        txt = "<table cellpadding='4'>"
        for icon, desc in rows:
            txt += (f"<tr><td style='font-size:16pt'>{icon}</td>"
                    f"<td>{desc}</td></tr>")
        txt += "</table>"
        QMessageBox.information(self, _t("MIB tree icons"), txt)

    def _show_shortcuts(self) -> None:
        rows = [
            ("Ctrl+L",       _t("Load MIB…")),
            ("Ctrl+,",       _t("Preferences…")),
            ("Ctrl+F",       _t("Find in MIB tree")),
            ("Ctrl+T",       _t("Find in Result")),
            ("Ctrl+S",       _t("Save results as CSV…")),
            ("Ctrl+Q",       _t("Exit")),
            ("Ctrl+Return",  _t("Run / Refresh") + " (" + _t("Go ▶") + ")"),
            ("F5",           _t("Run / Refresh")),
            ("Escape",       _t("Stop")),
            ("Ctrl+D",       _t("Bookmark current OID…")),
        ]
        txt = "<table cellpadding='3'>"
        for k, v in rows:
            txt += f"<tr><td><b><tt>{k}</tt></b></td><td>{v}</td></tr>"
        txt += "</table>"
        QMessageBox.information(self, _t("Keyboard shortcuts…"), txt)

    def _open_preferences(self) -> None:
        from .prefs_dialog import PreferencesDialog
        prev_single_root = self.settings.single_tree_root
        d = PreferencesDialog(self.settings, self)
        if d.exec():
            # Reflect changes in the toolbar; language needs restart (dialog
            # shows that hint next to the combo).
            ag = self.settings.current_agent
            self.version_combo.setCurrentText(ag.version)
            self.comm_edit.setText(ag.read_community)
            self._refresh_agent_status()
            # single_tree_root affects tree structure — rebuild if toggled.
            if self.settings.single_tree_root != prev_single_root:
                self._reload_mib_tree()

    def _open_mib_modules(self) -> None:
        from .mib_modules_dialog import MibModulesDialog
        d = MibModulesDialog(self.settings, self)
        if d.exec() and d.changed:
            self._reload_mib_tree()

    def _open_log_file(self) -> None:
        """Reveal the log file — prefer xdg-open for user's tail viewer,
        fall back to built-in read-only text viewer."""
        from .. import config
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        path = config.log_file()
        if not path.exists():
            QMessageBox.information(self, _t("Open log file…"),
                                    f"{path} (not yet created)")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            # Fallback: show last 500 lines inline.
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit
            dlg = QDialog(self); dlg.setWindowTitle(str(path))
            dlg.resize(900, 600)
            layout = QVBoxLayout(dlg)
            ed = QPlainTextEdit(); ed.setReadOnly(True)
            try:
                lines = path.read_text(errors="replace").splitlines()[-500:]
                ed.setPlainText("\n".join(lines))
            except Exception as exc:
                ed.setPlainText(f"error: {exc}")
            layout.addWidget(ed)
            dlg.exec()

    def _swap_tree(self, new_tree: MibTree) -> None:
        """Replace the live MIB tree and refresh dependent widgets."""
        self.tree = new_tree
        self.mib_model = MibTreeModel(
            self.tree, single_root=self.settings.single_tree_root)
        self.mib_proxy.setSourceModel(self.mib_model)
        self.result_model.set_tree(self.tree)

    def _reload_mib_tree(self) -> None:
        from .. import config, mib_loader
        new_tree = mib_loader.MibTree()
        new_tree.load_compiled(config.compiled_mibs_dir(),
                               enabled=self.settings.enabled_mibs)
        self._swap_tree(new_tree)
        self.status.showMessage(
            f"MIB tree reloaded: {len(self.tree.modules)} modules, "
            f"{len(self.tree._by_name)} names")
        if new_tree.missing_enabled:
            self._log(
                f"!!! {len(new_tree.missing_enabled)} enabled MIB modules "
                f"missing from cache: {', '.join(new_tree.missing_enabled)}. "
                f"See log: {config.log_file()}", level="warn")

    def _rebuild_mibs(self) -> None:
        """Full MIB-cache rebuild, determinate progress per-module."""
        from PyQt6.QtCore import QObject, QThread, pyqtSignal
        from PyQt6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QVBoxLayout
        from .. import mib_loader

        # Tiny modal: confirm + offer network fallback. Local-only by default.
        d = QDialog(self); d.setWindowTitle(_t("Recompile all MIBs…"))
        dv = QVBoxLayout(d)
        warn = QLabel(
            _t("This wipes the compiled cache and recompiles every MIB "
               "file found under mibs-src/ (and any extra source dirs). "
               "Can take 30–90s. Active MIB tree will reload afterwards."))
        warn.setWordWrap(True)
        dv.addWidget(warn)
        net_chk = QCheckBox(
            _t("Fetch missing dependencies from mibs.pysnmp.com "
               "(slower, needs internet)"))
        net_chk.setChecked(self.settings.fetch_missing_from_net)
        dv.addWidget(net_chk)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(d.accept); btns.rejected.connect(d.reject)
        dv.addWidget(btns)
        if not d.exec():
            return
        use_network = net_chk.isChecked()
        if use_network != self.settings.fetch_missing_from_net:
            self.settings.fetch_missing_from_net = use_network
            self.settings.save()

        class _RebuildWorker(QObject):
            progress = pyqtSignal(str, str, int, int)   # mod,status,done,total
            done = pyqtSignal(object)
            failed = pyqtSignal(str)
            def run(self):
                try:
                    def cb(mod, status, done, total):
                        self.progress.emit(mod, str(status), done, total)
                    t = mib_loader.build_tree_with_default_mibs(
                        rebuild=True, on_progress=cb,
                        use_network=use_network)
                    self.done.emit(t)
                except Exception as e:
                    self.failed.emit(str(e))

        thread = QThread(self)
        worker = _RebuildWorker()
        worker.moveToThread(thread)
        thread._worker_ref = worker

        def on_progress(mod, status, done, total):
            # First signal sets the total; subsequent ones advance.
            if self.progress.maximum() == 0 or self.progress.maximum() != total:
                self.progress.setRange(0, total)
                self.progress.setTextVisible(True)
                self.progress.setFormat("%v / %m (%p%)")
            self._advance_progress(done,
                f"Rebuild: [{done}/{total}] {mod} — {status}")
        def on_done(new_tree):
            # We know how many modules were in mibs-src/ (we iterate them).
            # pysmi may have pulled in additional transitive deps — count
            # them separately so the user isn't confused.
            from .. import mib_loader as _ml
            from .. import config as _cfg
            src_modules = set(
                _ml._discover_modules([_cfg.default_mibs_src()]))
            total_loaded = len(new_tree.modules)
            source_count = len(src_modules & new_tree.modules.keys())
            deps_count = total_loaded - source_count
            self._swap_tree(new_tree)
            msg = (f"MIB cache rebuilt: {source_count} from source"
                   f" (+{deps_count} dependencies), {total_loaded} total, "
                   f"{len(new_tree._by_name)} symbols")
            self._end_progress(msg)
            self._log(f"<<< Rebuild finished: {msg}")
        def on_failed(msg):
            self._end_progress(f"Rebuild failed: {msg}")
            self._log(f"!!! Rebuild failed: {msg}")
            QMessageBox.warning(self, _t("Recompile all MIBs…"), msg)

        thread.started.connect(worker.run)
        worker.progress.connect(on_progress)
        worker.done.connect(on_done)
        worker.failed.connect(on_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._log(">>> Rebuild: starting full MIB recompile")
        # Start determinate (0..100) — the first progress signal will set the
        # true total in the range change above.
        self._begin_progress("Rebuilding MIB cache…", total=100)
        self._active_threads.append(thread)
        thread.start()

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _about(self) -> None:
        from .. import __version__
        QMessageBox.about(self, _t("About") + " pymibbrowser",
                          f"<h3>pymibbrowser {__version__}</h3>"
                          "Python port of iReasoning MIB Browser.<br>"
                          "PyQt6 · pysnmp · pysmi · pyqtgraph.")

    def _switch_lang(self, lang: str) -> None:
        set_language(lang)
        self.settings.language = lang
        self.settings.save()
        QMessageBox.information(
            self, _t("About"),
            f"Language saved ({lang}). Restart the app to apply fully.\n"
            f"Язык сохранён ({lang}). Перезапустите приложение.")

    _LOG_COLORS = {
        "req":   "#6cf",      # outgoing SNMP request "  >>>"
        "resp":  "#9e9",      # "  <<<" finished OK
        "hint":  "#fc6",      # "hint: …"
        "warn":  "#fa3",
        "err":   "#f66",
        "info":  "#bbb",
    }

    def _log(self, text: str, level: str = "info") -> None:
        if self.log_widget is None:
            return
        color = self._LOG_COLORS.get(level, "#bbb")
        # Escape HTML entities and keep whitespace (pre-wrap via <span>).
        escaped = (text.replace("&", "&amp;").replace("<", "&lt;")
                        .replace(">", "&gt;"))
        # Auto-detect log level from conventional prefixes used throughout
        # the app, so existing callers keep working without passing level=…
        if level == "info":
            if text.startswith(">>>"):
                color = self._LOG_COLORS["req"]
            elif text.startswith("<<<"):
                color = self._LOG_COLORS["resp"]
            elif text.startswith("!!!"):
                color = self._LOG_COLORS["err"]
        self.log_widget.appendHtml(
            f'<span style="color:{color}; white-space:pre">{escaped}</span>')

    def closeEvent(self, event) -> None:
        """Orderly shutdown — stops every tab's polling timer and waits
        for in-flight SNMP workers to finish before Qt starts tearing
        down QThreads. Without this, a thread still inside pysnmp at
        close time gets destroyed while running, which aborts the
        process ('QThread: Destroyed while thread is still running').
        """
        try:
            # Flush any debounced save that hasn't fired yet.
            self._settings_save_timer.stop()
            self.settings.save()
        finally:
            # Give each dynamic tab a chance to stop its timer.
            for i in range(self.tabs.count()):
                w = self.tabs.widget(i)
                for attr in ("_timer", "_poll_timer"):
                    t = getattr(w, attr, None)
                    if t is not None and hasattr(t, "stop"):
                        try:
                            t.stop()
                        except Exception:
                            pass
            # Wait (briefly) for active SNMP threads the main window and
            # each tab spun off. 800ms budget each — plenty for a GET,
            # and we'd rather close slightly late than abort.
            from PyQt6.QtCore import QThread
            pools: list[list] = [self._active_threads]
            for i in range(self.tabs.count()):
                w = self.tabs.widget(i)
                at = getattr(w, "_active_threads", None)
                if isinstance(at, list):
                    pools.append(at)
            for pool in pools:
                for t in pool:
                    if isinstance(t, QThread) and t.isRunning():
                        try:
                            t.wait(800)
                        except Exception:
                            pass
            super().closeEvent(event)
