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
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
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

        self.mib_search = QLineEdit()
        self.mib_search.setPlaceholderText(_t("Filter MIB tree (substring)…"))
        self.mib_search.setClearButtonEnabled(True)
        # Debounce: applying the filter on every keystroke across ~5 000 nodes
        # with recursive filtering freezes the UI. Wait until the user has
        # paused typing for 250 ms before reapplying.
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self._apply_mib_filter_now)
        self.mib_search.textChanged.connect(lambda _: self._filter_timer.start())
        tv.addWidget(self.mib_search)

        self.mib_model = MibTreeModel(self.tree)
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

        self.result_tab = self._make_result_tab()
        self.tabs.addTab(self.result_tab, _t("Result"))
        # Result tab can't be closed
        self.tabs.tabBar().setTabButton(0, self.tabs.tabBar().ButtonPosition.RightSide, None)

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
        layout.addWidget(self.result_split, 1)
        return w

    # ------------------------------------------------------------------
    # Toolbar: address, OID, operation, Go
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)

        tb.addWidget(QLabel(_t(" Address: ")))
        self.addr_edit = QLineEdit(
            f"{self.settings.current_agent.host}:{self.settings.current_agent.port}")
        self.addr_edit.setMinimumWidth(200)
        tb.addWidget(self.addr_edit)

        adv = QPushButton(_t("Advanced…"))
        adv.clicked.connect(self._open_agent_dialog)
        tb.addWidget(adv)

        tb.addSeparator()
        tb.addWidget(QLabel(_t(" OID: ")))
        self.oid_edit = QLineEdit()
        self.oid_edit.setMinimumWidth(340)
        self.oid_edit.returnPressed.connect(self._run_operation)
        tb.addWidget(self.oid_edit)

        tb.addSeparator()
        tb.addWidget(QLabel(_t(" Operation: ")))
        self.op_combo = QComboBox()
        self.op_combo.addItems(OPERATIONS)
        self.op_combo.setCurrentText("Get")
        tb.addWidget(self.op_combo)

        go = QPushButton(_t("Go ▶"))
        go.setShortcut(QKeySequence("Ctrl+Return"))
        go.clicked.connect(self._run_operation)
        tb.addWidget(go)

        self.addToolBar(tb)

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        mb = self.menuBar()

        file_m = mb.addMenu(_t("&File"))
        file_m.addAction(self._action(_t("Load MIB…"), self._load_mibs, "Ctrl+L"))
        file_m.addAction(self._action(_t("MIB Modules…"), self._open_mib_modules))
        file_m.addAction(self._action(_t("Rebuild MIB cache"), self._rebuild_mibs))
        file_m.addSeparator()
        file_m.addAction(self._action(_t("Open log file…"), self._open_log_file))
        file_m.addSeparator()
        file_m.addAction(self._action(_t("Save session (CSV)…"), self._save_csv, "Ctrl+S"))
        file_m.addAction(self._action(_t("Exit"), self.close, "Ctrl+Q"))

        edit_m = mb.addMenu(_t("&Edit"))
        edit_m.addAction(self._action(_t("Find in MIB tree"), self._find_in_tree, "Ctrl+F"))
        edit_m.addAction(self._action(_t("Find in Result"), self._focus_result_find, "Ctrl+T"))

        ops_m = mb.addMenu(_t("&Operations"))
        for op in OPERATIONS:
            a = self._action(op, lambda _=False, o=op: self._run_operation(o))
            ops_m.addAction(a)
        ops_m.addSeparator()
        ops_m.addAction(self._action(_t("Table View…"), self._open_table_view))
        ops_m.addAction(self._action(_t("Graph…"), self._open_graph))

        tools_m = mb.addMenu(_t("&Tools"))
        tools_m.addAction(self._action(_t("Trap Receiver…"), self._open_trap_receiver))
        tools_m.addAction(self._action(_t("Trap Sender…"), self._open_trap_sender))
        tools_m.addAction(self._action(_t("Agent Simulator…"), self._open_agent_simulator))
        tools_m.addAction(self._action(_t("MIB Editor…"), self._open_mib_editor))
        tools_m.addAction(self._action(_t("Trapd Console…"), self._open_trapd_console))
        tools_m.addAction(self._action(_t("Run Script…"), self._run_script))

        help_m = mb.addMenu(_t("&Help"))
        lang_m = help_m.addMenu(_t("Language"))
        lang_m.addAction(self._action("English", lambda: self._switch_lang("en")))
        lang_m.addAction(self._action("Русский", lambda: self._switch_lang("ru")))
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
        # Right-side progress widget: hidden by default, shown while
        # long-running ops are in flight. Busy mode (no total) = animated
        # indeterminate bar; with a total = 0..100 % determinate.
        self.progress = QProgressBar()
        self.progress.setFixedWidth(200)
        self.progress.setTextVisible(False)
        self.progress.hide()
        sb.addPermanentWidget(self.progress)
        self._progress_depth = 0
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
        s = self.addr_edit.text().strip()
        if ":" in s:
            host, port_s = s.rsplit(":", 1)
            return host, int(port_s)
        if "@" in s:
            host, port_s = s.rsplit("@", 1)
            return host, int(port_s)
        return s, 161

    def current_agent(self) -> Agent:
        ag = self.settings.current_agent
        try:
            host, port = self._parse_address()
        except ValueError:
            host, port = ag.host, ag.port
        ag.host = host
        ag.port = port
        return ag

    def _open_agent_dialog(self) -> None:
        from .dialogs import AgentDialog
        d = AgentDialog(self.current_agent(), self)
        if d.exec():
            ag = d.agent
            self.settings.current_agent = ag
            self.addr_edit.setText(f"{ag.host}:{ag.port}")
            self.settings.save()
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
        if n.is_scalar:
            return "Get"
        if n.is_column:
            return "Walk"
        if n.is_table or n.is_table_entry:
            return "Get Subtree"
        return "Get Next"

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
        n = self._node_for_proxy_index(index)
        if n is None:
            return
        self.oid_edit.setText(self._instance_oid(n))
        if n.is_table or n.is_table_entry:
            self._open_table_view()
            return
        # Fire the same default op we already set in the toolbar — so GET on
        # a scalar, WALK on a column, etc.
        op = self._default_op_for(n)
        self.op_combo.setCurrentText(op)
        self._run_operation(op)

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
        m.addAction("Copy OID", lambda: QApplication.clipboard().setText(
            "." + ".".join(str(p) for p in n.oid)))
        m.addAction("Copy Name", lambda: QApplication.clipboard().setText(n.name))
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

    def _find_in_tree(self) -> None:
        # Ctrl+F now focuses the inline filter. Exact-name find is redundant
        # with substring filtering — user can type a prefix and see the match.
        self.mib_search.setFocus()
        self.mib_search.selectAll()

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
        agent = self.current_agent()
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
                self._log("    " + _t(
                    "hint: that OID has no scalar instance. For a column, "
                    "use Walk or Get Next; for a table, use Get Subtree or "
                    "open Table View."), level="hint")
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
        self.tabs.removeTab(idx)
        w.deleteLater()

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

    def _open_graph(self) -> None:
        from .graph_window import GraphTab
        oid_text = self.oid_edit.text().strip()
        if not oid_text:
            QMessageBox.warning(self, "Graph", "Select a numeric OID.")
            return
        resolved = self.tree.resolve_name(oid_text)
        if resolved is None:
            return
        agent = self.current_agent()
        tab = GraphTab(self, agent, resolved, oid_text)
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
        path, _ = QFileDialog.getOpenFileName(self, "Run script", "", "Scripts (*.txt *.snmp)")
        if not path:
            return
        from .. import script_runner
        try:
            script_runner.run(path, self.current_agent(), self.tree,
                              logger=self._log)
        except Exception as exc:
            QMessageBox.warning(self, "Script", str(exc))

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
        self.mib_model = MibTreeModel(self.tree)
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
        d = QDialog(self); d.setWindowTitle(_t("Rebuild MIB cache"))
        dv = QVBoxLayout(d)
        dv.addWidget(QLabel(
            _t("Recompile every MIB file in mibs-src/ (overwrites the cache).")))
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
            QMessageBox.warning(self, _t("Rebuild MIB cache"), msg)

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
        try:
            self.settings.save()
        finally:
            super().closeEvent(event)
