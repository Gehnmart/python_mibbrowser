"""Capture UI screenshots for docs/guide/.

Runs headless (QT_QPA_PLATFORM=offscreen) and walks every significant
screen, populating each widget with synthetic but realistic data so
screenshots are never empty. Idempotent — one command regenerates the
whole set.

Usage:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/capture_screenshots.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt                 # noqa: E402
from PyQt6.QtGui import QBrush, QColor      # noqa: E402
from PyQt6.QtWidgets import (                # noqa: E402
    QApplication, QDialog, QTabWidget, QTableWidgetItem,
)

OUT_DIR = ROOT / "docs" / "guide" / "screenshots"


def _pump(times: int = 6) -> None:
    """Let Qt paint. Multiple passes because layout → paint → repaint."""
    app = QApplication.instance()
    assert app is not None
    for _ in range(times):
        app.processEvents()


def _save(widget, name: str, size=None) -> None:
    if size is not None:
        widget.resize(*size)
    widget.show()
    _pump()
    pix = widget.grab()
    path = OUT_DIR / f"{name}.png"
    pix.save(str(path), "PNG")
    print(f"  → {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")


# ---------------------------------------------------------------------------
# Data seeders — inject realistic content directly into widgets
# ---------------------------------------------------------------------------

def _seed_result_pane(w):
    """Fill the Result tab with a mix of Walk output for ifTable."""
    from pymibbrowser.snmp_ops import VarBind

    samples = [
        ((1, 3, 6, 1, 2, 1, 1, 1, 0), "OctetString",
         "Linux lab 6.8.0-110-generic #Ubuntu"),
        ((1, 3, 6, 1, 2, 1, 1, 3, 0), "TimeTicks",
         "2 days 17 hours 4 minutes (23544000)"),
        ((1, 3, 6, 1, 2, 1, 1, 5, 0), "OctetString", "core-sw-01"),
        ((1, 3, 6, 1, 2, 1, 1, 6, 0), "OctetString", "rack 3B, row 2"),
        ((1, 3, 6, 1, 2, 1, 2, 2, 1, 2, 1), "OctetString", "lo"),
        ((1, 3, 6, 1, 2, 1, 2, 2, 1, 2, 2), "OctetString", "eth0"),
        ((1, 3, 6, 1, 2, 1, 2, 2, 1, 2, 3), "OctetString", "eth1"),
        ((1, 3, 6, 1, 2, 1, 2, 2, 1, 8, 1), "Integer", "up(1)"),
        ((1, 3, 6, 1, 2, 1, 2, 2, 1, 8, 2), "Integer", "up(1)"),
        ((1, 3, 6, 1, 2, 1, 2, 2, 1, 8, 3), "Integer", "down(2)"),
        ((1, 3, 6, 1, 2, 1, 2, 2, 1, 10, 1), "Counter32", "7984562"),
        ((1, 3, 6, 1, 2, 1, 2, 2, 1, 10, 2), "Counter32", "14302918"),
    ]
    for oid, tn, dv in samples:
        w.result_model.add(
            VarBind(oid=oid, type_name=tn, value=None, display_value=dv),
            "127.0.0.1:161",
        )
    # Log lines so the log pane isn't empty either.
    w._log(">>> Walk .1.3.6.1.2.1.2.2  @ 127.0.0.1:161")
    w._log("<<< Walk finished: 12 varbind(s)")
    w._log("    hint: for a column, use Walk or Get Next; for a table, "
           "use Get Subtree or open Table View.", level="hint")
    _pump()


def _seed_graph(tab, *, points: int = 50, rate_mode: bool = True):
    """Paint a synthetic sine-like curve onto a GraphTab."""
    import math
    tab._timer.stop()
    tab._t.clear(); tab._v.clear()
    base = 10_000_000
    for i in range(points):
        t = i * 3.0
        val = base + 200_000 * math.sin(i / 6.0) + 15_000 * (i % 3)
        tab._t.append(t)
        tab._v.append(val)
    tab._t0 = 0
    tab.curve.setData(list(tab._t), list(tab._v))
    tab._rate_mode = rate_mode
    tab.rate_chk.setChecked(rate_mode)
    tab.plot.setTitle("ifInOctets.1 (rate)")
    tab.status_label.setText("latest=2.3 Mbps  samples=50")


def _seed_port_view(tab):
    """Add a handful of PortTile widgets with up/down badges and bars."""
    from pymibbrowser.ui.port_view_tab import PortTile
    demo = [
        (1,  "lo",           "1", 0.4,  10_000_000,   "softwareLoopback", 40_000),
        (2,  "enp1s0",       "1", 12.3, 1_000_000_000, "ethernetCsmacd", 123_000_000),
        (3,  "enp2s0",       "1", 0.02, 1_000_000_000, "ethernetCsmacd", 200_000),
        (4,  "enp2s0.100",   "1", 3.1,  1_000_000_000, "ethernetCsmacd", 31_000_000),
        (5,  "docker0",      "2", None, 0,            "bridge",         None),
        (6,  "vxlan-backup", "1", 68.0, 100_000_000,  "other",           68_000_000),
        (7,  "throne-tun",   "1", None, 0,            "tunnel",          7_200_000),
        (8,  "wg0",          "1", None, 0,            "other",           1_500_000),
    ]
    for idx, descr, status, util, speed, typ, bps in demo:
        t = PortTile(if_index=idx)
        t.clicked.connect(tab._on_tile_click)
        tab._tiles[idx] = t
        t.update_state(descr=descr, status=status, util_pct=util,
                       speed_bps=speed, if_type=typ, throughput_bps=bps)
    tab._relayout()
    tab.status.setText("8 interface(s) · updated every 2s")


def _seed_watches(tab):
    """Populate WatchesTab rows with coloured normal / alarm / n/a."""
    from datetime import datetime
    stamp = datetime.now().strftime("%H:%M:%S")
    # Fill table model to match settings.watches length.
    tab._refill()
    demo = [
        ("sysUpTime",          "23544000",  "normal", True),
        ("ifInOctets.1",       "7984562",   "normal", True),
    ]
    extra = [
        ("ifOperStatus.3",     "2",         "alarm",  False),
        ("hrProcessorLoad.1",  "92",        "alarm",  False),
        ("sysServices",        "-",         "n/a",    None),
    ]
    # Seed extra watches directly onto the table (beyond settings.watches).
    from PyQt6.QtWidgets import QTableWidgetItem
    for (name, _val, _st, _ok) in extra:
        r = tab.tbl.rowCount()
        tab.tbl.insertRow(r)
        tab.tbl.setItem(r, 0, QTableWidgetItem(name))
        for c in range(1, 5):
            tab.tbl.setItem(r, c, QTableWidgetItem(""))
    all_rows = demo + extra
    for r, (_name, val, status, ok) in enumerate(all_rows):
        # Condition column mirrors what settings would store.
        tab.tbl.item(r, 2).setText(
            ("> 0" if ok is True else "< 50" if ok is False else "== up"))
        tab._paint_row(r, val, status, ok, stamp)
    tab.status_label.setText("Watching 5 OIDs · " + stamp)


def _seed_poll_tab(tab):
    """Fill PollTab cells with values for each agent×variable."""
    tab._timer.stop()
    for r in range(tab.tbl.rowCount()):
        for c in range(1, tab.tbl.columnCount()):
            txt = "4 days 12 hours" if c == 1 else "core-sw-01"
            if r > 0:
                txt = "1 day 3 hours"  if c == 1 else "edge-rtr-02"
            tab.tbl.item(r, c).setText(txt)
    tab.status_label.setText("Polling 2 agent(s)…")


def _seed_device_snapshot(tab):
    """Populate all three panels directly without issuing SNMP."""
    from PyQt6.QtWidgets import QTableWidgetItem
    from PyQt6.QtGui import QFont
    def _fill_kv(tbl, rows):
        tbl.setRowCount(len(rows))
        for r, (k, v) in enumerate(rows):
            key = QTableWidgetItem(k + ":")
            f = key.font(); f.setBold(True); key.setFont(f)
            tbl.setItem(r, 0, key)
            tbl.setItem(r, 1, QTableWidgetItem(v))
        tbl.resizeColumnsToContents()
        tbl.horizontalHeader().setStretchLastSection(True)
    _fill_kv(tab.basic_tbl, [
        ("sysName",      "core-sw-01"),
        ("sysDescr",     "Linux lab 6.8 Intel Xeon 2.8GHz"),
        ("sysUpTime",    "2 days 17 hours 4 minutes"),
        ("sysContact",   "noc@example.com"),
        ("sysLocation",  "DC3, rack 12"),
        ("sysObjectID",  ".1.3.6.1.4.1.8072.3.2.10"),
    ])
    _fill_kv(tab.hr_tbl, [
        ("hrSystemProcesses", "248"),
        ("hrMemorySize",      "16 GB"),
    ])
    # interfaces
    tab.if_tbl.setRowCount(4)
    if_rows = [
        ("1", "lo",     "softwareLoopback", "10 Mbps",   "up"),
        ("2", "eth0",   "ethernetCsmacd",   "1 Gbps",    "up"),
        ("3", "eth1",   "ethernetCsmacd",   "1 Gbps",    "down"),
        ("4", "wg0",    "other",            "0",         "up"),
    ]
    for r, row in enumerate(if_rows):
        for c, v in enumerate(row):
            item = QTableWidgetItem(v)
            if c == 4:
                item.setForeground(
                    Qt.GlobalColor.darkGreen if v == "up"
                    else Qt.GlobalColor.darkRed)
            tab.if_tbl.setItem(r, c, item)
    tab.if_tbl.resizeColumnsToContents()
    tab.if_tbl.horizontalHeader().setStretchLastSection(True)
    tab.status.setText("Snapshot from 127.0.0.1:161 · 4 interfaces")


def _seed_save_walk(dlg):
    """Populate the preview with sample walk lines."""
    text = "\n".join([
        '.1.3.6.1.2.1.1.1.0 = STRING: "Linux lab 6.8.0-110-generic"',
        '.1.3.6.1.2.1.1.2.0 = OID: .1.3.6.1.4.1.8072.3.2.10',
        '.1.3.6.1.2.1.1.3.0 = Timeticks: 23544000',
        '.1.3.6.1.2.1.1.4.0 = STRING: "noc@example.com"',
        '.1.3.6.1.2.1.1.5.0 = STRING: "core-sw-01"',
        '.1.3.6.1.2.1.1.6.0 = STRING: "DC3, rack 12"',
        '.1.3.6.1.2.1.2.2.1.2.1 = STRING: "lo"',
        '.1.3.6.1.2.1.2.2.1.2.2 = STRING: "eth0"',
        '.1.3.6.1.2.1.2.2.1.2.3 = STRING: "eth1"',
        '.1.3.6.1.2.1.2.2.1.8.1 = INTEGER: up(1)',
        '.1.3.6.1.2.1.2.2.1.8.2 = INTEGER: up(1)',
        '.1.3.6.1.2.1.2.2.1.8.3 = INTEGER: down(2)',
        '.1.3.6.1.2.1.2.2.1.10.1 = Counter32: 7984562',
        '.1.3.6.1.2.1.2.2.1.10.2 = Counter32: 14302918',
    ])
    dlg.preview.setPlainText(text)
    dlg.save_b.setEnabled(True)
    dlg.status.setText("Walk finished — 14 rows")


def _seed_compare(dlg):
    """Fill compare-diff table with equal / only-left / only-right / diff."""
    # Fake two walks
    left = {
        (1, 3, 6, 1, 2, 1, 1, 1, 0): "Linux lab 6.8",
        (1, 3, 6, 1, 2, 1, 1, 3, 0): "23544000",
        (1, 3, 6, 1, 2, 1, 1, 5, 0): "core-sw-01",
        (1, 3, 6, 1, 2, 1, 1, 6, 0): "DC3, rack 12",
        (1, 3, 6, 1, 2, 1, 2, 2, 1, 8, 3): "up",
    }
    right = {
        (1, 3, 6, 1, 2, 1, 1, 1, 0): "Linux lab 6.8",
        (1, 3, 6, 1, 2, 1, 1, 3, 0): "23544000",
        (1, 3, 6, 1, 2, 1, 1, 5, 0): "edge-sw-02",
        (1, 3, 6, 1, 2, 1, 1, 7, 0): "74",
        (1, 3, 6, 1, 2, 1, 2, 2, 1, 8, 3): "down",
    }
    dlg._left = left; dlg._right = right
    dlg.hide_equal.setChecked(False)
    dlg._render()


def _seed_discovery(dlg):
    """Inject a handful of live-host rows into the discovery table."""
    from PyQt6.QtWidgets import QTableWidgetItem
    demo = [
        ("192.168.1.1",   "0.6", "edge-rtr",     "Cisco IOS XE 17.09"),
        ("192.168.1.10",  "0.2", "core-sw-01",   "Linux lab 6.8"),
        ("192.168.1.11",  "0.3", "core-sw-02",   "Linux lab 6.8"),
        ("192.168.1.50",  "1.1", "printer-1",    "HP LaserJet 4250dn"),
        ("192.168.1.100", "12",  "nas-storage",  "TrueNAS SCALE 24.04"),
        ("192.168.1.200", "0.4", "",             ""),
    ]
    for ip, rtt, name, descr in demo:
        r = dlg.tbl.rowCount()
        dlg.tbl.insertRow(r)
        chk = QTableWidgetItem()
        chk.setFlags(chk.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        chk.setCheckState(Qt.CheckState.Checked if name
                           else Qt.CheckState.Unchecked)
        dlg.tbl.setItem(r, 0, chk)
        dlg.tbl.setItem(r, 1, QTableWidgetItem(ip))
        rtt_item = QTableWidgetItem(rtt)
        rtt_item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        dlg.tbl.setItem(r, 2, rtt_item)
        dlg.tbl.setItem(r, 3, QTableWidgetItem(name))
        dlg.tbl.setItem(r, 4, QTableWidgetItem(descr))
    dlg.bar.setMaximum(254); dlg.bar.setValue(254)
    dlg.status.setText("Found 6 live host(s)")


def _seed_ping(dlg):
    dlg.output.appendPlainText("$ ping -n -c 5 1.1.1.1")
    for ln in [
        "PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.",
        "64 bytes from 1.1.1.1: icmp_seq=1 ttl=58 time=2.48 ms",
        "64 bytes from 1.1.1.1: icmp_seq=2 ttl=58 time=2.51 ms",
        "64 bytes from 1.1.1.1: icmp_seq=3 ttl=58 time=2.39 ms",
        "64 bytes from 1.1.1.1: icmp_seq=4 ttl=58 time=2.55 ms",
        "64 bytes from 1.1.1.1: icmp_seq=5 ttl=58 time=2.44 ms",
        "",
        "--- 1.1.1.1 ping statistics ---",
        "5 packets transmitted, 5 received, 0% packet loss, time 4007ms",
        "rtt min/avg/max/mdev = 2.39/2.47/2.55/0.07 ms",
    ]:
        dlg.output.appendPlainText(ln)
    dlg.status.setText("Finished (exit 0)")


def _seed_traceroute(dlg):
    dlg.output.appendPlainText("$ tracepath -n 1.1.1.1")
    for ln in [
        " 1?: [LOCALHOST]                      pmtu 1500",
        " 1:  192.168.1.1                     0.621 ms",
        " 2:  10.0.0.1                        3.112 ms",
        " 3:  100.64.0.1                      4.557 ms",
        " 4:  141.98.14.25                    9.834 ms",
        " 5:  172.16.48.17                    11.422 ms",
        " 6:  1.1.1.1                         13.001 ms  reached",
        "     Resume: pmtu 1500 hops 6 back 6",
    ]:
        dlg.output.appendPlainText(ln)
    dlg.status.setText("Finished (exit 0)")


def _seed_trap_receiver(w):
    """Inject synthetic TrapEvents into the model."""
    from pymibbrowser.trap_receiver import TrapEvent
    import time as _time
    now = _time.time()
    samples = [
        TrapEvent(time=now - 3600, source_ip="192.168.1.1",
                  source_port=51342, version="2c", community="public",
                  trap_oid=".1.3.6.1.6.3.1.1.5.3",
                  severity="HIGH", message="linkDown ifIndex=3"),
        TrapEvent(time=now - 1800, source_ip="192.168.1.10",
                  source_port=51343, version="2c", community="public",
                  trap_oid=".1.3.6.1.6.3.1.1.5.4",
                  severity="INFO", message="linkUp ifIndex=3"),
        TrapEvent(time=now - 120, source_ip="192.168.1.50",
                  source_port=51001, version="1", community="public",
                  trap_oid=".1.3.6.1.4.1.1347.1.1.5",
                  severity="MEDIUM", message="HP printer: paper jam"),
        TrapEvent(time=now - 5, source_ip="192.168.1.10",
                  source_port=51344, version="2c", community="public",
                  trap_oid=".1.3.6.1.4.1.2021.251.1",
                  severity="CRITICAL", message="UCD: Disk / 96% full"),
    ]
    for ev in samples:
        w.model.add(ev)
    # select the latest so details panel is populated
    if w.table.model().rowCount() > 0:
        w.table.selectRow(0)
    w.statusBar().showMessage(
        f"Listening on 0.0.0.0:{w.settings.trap_port}  ·  "
        f"{len(samples)} trap(s) received")


def _seed_trap_sender(dlg):
    """Add typed var-bindings."""
    rows = [
        ("ifIndex",      "i", "3"),
        ("ifAdminStatus", "i", "2"),
        ("sysLocation",  "s", "rack 12"),
    ]
    for oid, typ, val in rows:
        dlg._add_row()
        r = dlg.vbs_table.rowCount() - 1
        dlg.vbs_table.item(r, 0).setText(oid)
        cb = dlg.vbs_table.cellWidget(r, 1)
        cb.setCurrentText(typ)
        dlg.vbs_table.item(r, 2).setText(val)
    dlg.status.setText("→ ready to send SNMPv2 trap")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)

    from pymibbrowser.i18n import init_language
    from pymibbrowser.config import (
        AppSettings, Agent, WatchDefinition, PollDefinition, PollVariable,
        compiled_mibs_dir,
    )
    from pymibbrowser.mib_loader import MibTree

    # RU for matching russian guide text.
    init_language("ru")

    settings = AppSettings()
    settings.current_agent = Agent(host="127.0.0.1", port=161,
                                    read_community="public")
    settings.saved_agents = [
        Agent(host="core-sw-01", port=161, read_community="public"),
        Agent(host="edge-rtr-02", port=161, read_community="public"),
        Agent(host="edge-sw-03", port=161, read_community="public"),
    ]
    settings.watches = [
        WatchDefinition(name="sysUpTime", oid=".1.3.6.1.2.1.1.3.0",
                         operation="Get", condition_op=">",
                         condition_value="0"),
        WatchDefinition(name="ifInOctets.1",
                         oid=".1.3.6.1.2.1.2.2.1.10.1",
                         operation="Get", condition_op="<",
                         condition_value="100000000"),
    ]
    settings.polls = [
        PollDefinition(
            name="Uptime sweep", interval_s=30,
            agents=["127.0.0.1:161", "core-sw-01:161"],
            variables=[
                PollVariable(name="sysUpTime", oid=".1.3.6.1.2.1.1.3.0"),
                PollVariable(name="sysName",   oid=".1.3.6.1.2.1.1.5.0"),
            ],
        )
    ]
    settings.bookmarks = [
        {"name": "Interfaces table", "oid": ".1.3.6.1.2.1.2.2",
         "operation": "", "view": "table"},
        {"name": "System uptime", "oid": ".1.3.6.1.2.1.1.3.0",
         "operation": "Get", "view": "op"},
        {"name": "Interface errors", "oid": ".1.3.6.1.2.1.2.2.1.14",
         "operation": "Walk", "view": "op"},
    ]
    settings.recent_oids = [
        ".1.3.6.1.2.1.1.3.0", "sysName.0", "ifInOctets",
        ".1.3.6.1.2.1.2.2", "hrSystemUptime.0",
    ]

    tree = MibTree()
    tree.load_compiled(compiled_mibs_dir(), enabled=None)

    from pymibbrowser.ui.main_window import MibBrowserWindow
    w = MibBrowserWindow(tree, settings)
    w.resize(1280, 820)
    w.oid_edit.setText("sysUpTime.0")
    w.op_combo.setCurrentText("Get")

    # --- main window empty --------------------------------------------
    _save(w, "main_window")

    # --- main window with data + expanded tree ------------------------
    from pymibbrowser.ui.mib_tree_model import MibTreeModel  # noqa: F401
    idx = w.mib_model.find_index((1, 3, 6, 1, 2, 1, 1))      # system
    if idx.isValid():
        proxy_idx = w.mib_proxy.mapFromSource(idx)
        w.mib_view.expand(proxy_idx)
        w.mib_view.scrollTo(proxy_idx)
    _seed_result_pane(w)
    _pump()
    _save(w, "main_window_filled")

    # --- MIB tree only (zoom) -----------------------------------------
    _save(w.mib_view, "mib_tree", size=(380, 560))

    # --- Properties panel zoom (after selecting a real node) ---------
    if idx.isValid():
        w.mib_view.setCurrentIndex(proxy_idx)
        n = w._node_for_proxy_index(proxy_idx)
        if n is not None:
            w._describe(n)
    _save(w.node_props, "node_properties", size=(380, 260))

    # --- Preferences --------------------------------------------------
    from pymibbrowser.ui.prefs_dialog import PreferencesDialog
    pd = PreferencesDialog(settings, w); pd.resize(720, 560)
    _save(pd, "preferences_general")
    tabs = pd.findChild(QTabWidget)
    if tabs is not None:
        tabs.setCurrentIndex(1); _pump()
        _save(pd, "preferences_snmp")
        tabs.setCurrentIndex(2); _pump()
        _save(pd, "preferences_mib")
        tabs.setCurrentIndex(3); _pump()
        _save(pd, "preferences_traps")
        tabs.setCurrentIndex(5); _pump()
        _save(pd, "preferences_logging")
    pd.close()

    # --- MIB Modules --------------------------------------------------
    from pymibbrowser.ui.mib_modules_dialog import MibModulesDialog
    mm = MibModulesDialog(settings, w); mm.resize(980, 620)
    # Select a well-known module so all three detail tabs have content.
    for r in range(mm.tbl.rowCount()):
        if mm.tbl.item(r, 1).text() == "IF-MIB":
            mm.tbl.selectRow(r); break
    _pump()
    _save(mm, "mib_modules")
    # General / Revisions / Imports detail tabs
    if hasattr(mm, "tabs"):
        mm.tabs.setCurrentIndex(1); _pump()
        _save(mm, "mib_modules_revisions")
        mm.tabs.setCurrentIndex(2); _pump()
        _save(mm, "mib_modules_imports")
    mm.close()

    # --- Manage agents ------------------------------------------------
    from pymibbrowser.ui.agents_dialog import AgentsDialog
    ad = AgentsDialog(settings, w); ad.resize(780, 360)
    if ad.tbl.rowCount() > 0:
        ad.tbl.selectRow(0)
    _save(ad, "manage_agents")
    ad.close()

    # --- Agent editor (modal) -----------------------------------------
    from pymibbrowser.ui.dialogs import AgentDialog
    agd = AgentDialog(Agent(host="core-sw-01", port=161,
                             read_community="public",
                             write_community="s3cret",
                             timeout_s=3.0, retries=2), w)
    _save(agd, "agent_editor", size=(520, 360))
    agd.close()

    # --- Bookmark dialogs (intercept exec) ----------------------------
    original_exec = QDialog.exec

    captured = {"dlg": None}
    def fake_exec(self):
        captured["dlg"] = self
        return 0
    QDialog.exec = fake_exec

    captured["dlg"] = None
    w._bookmark_current_oid()
    if captured["dlg"] is not None:
        _save(captured["dlg"], "bookmark_dialog", size=(480, 280))
        captured["dlg"].close()

    captured["dlg"] = None
    w._edit_bookmarks()
    if captured["dlg"] is not None:
        _save(captured["dlg"], "manage_bookmarks", size=(660, 400))
        captured["dlg"].close()

    QDialog.exec = original_exec

    # --- Poll dialog + Poll tab ---------------------------------------
    from pymibbrowser.ui.polls_dialog import PollDialog
    poll_dlg = PollDialog(settings.polls[0], settings,
                           tree=tree, parent=w)
    _save(poll_dlg, "create_poll", size=(620, 520))
    poll_dlg.close()

    from pymibbrowser.ui.poll_tab import PollTab
    pt = PollTab(w, settings.polls[0], settings, tree=tree)
    _seed_poll_tab(pt); _pump()
    _save(pt, "poll_tab", size=(820, 240))
    pt._timer.stop(); pt.close()

    # --- Watches tab --------------------------------------------------
    from pymibbrowser.ui.watches_tab import WatchesTab, HistoryDialog
    wt = WatchesTab(w, settings, lambda: settings.current_agent,
                     tree=tree)
    wt._timer.stop()
    _seed_watches(wt); _pump()
    _save(wt, "watches", size=(920, 360))
    # History dialog — fake two events into the jsonl
    from pymibbrowser.ui.watches_tab import _append_history_event
    _append_history_event({
        "ts": "2026-04-24T09:12:04", "watch": "ifOperStatus.3",
        "oid": ".1.3.6.1.2.1.2.2.1.8.3", "agent": "192.168.1.10:161",
        "value": "2", "state": "alarm", "from": "normal",
        "condition": "== 1"})
    _append_history_event({
        "ts": "2026-04-24T09:18:22", "watch": "ifOperStatus.3",
        "oid": ".1.3.6.1.2.1.2.2.1.8.3", "agent": "192.168.1.10:161",
        "value": "1", "state": "normal", "from": "alarm",
        "condition": "== 1"})
    _append_history_event({
        "ts": "2026-04-24T11:44:01", "watch": "hrProcessorLoad.1",
        "oid": ".1.3.6.1.2.1.25.3.3.1.2.1", "agent": "core-sw-01:161",
        "value": "92", "state": "alarm", "from": "normal",
        "condition": "< 80"})
    hd = HistoryDialog(wt)
    _save(hd, "watch_history", size=(820, 460))
    hd.close(); wt.close()

    # --- Device Snapshot ---------------------------------------------
    from pymibbrowser.ui.device_snapshot_tab import DeviceSnapshotTab
    ds = DeviceSnapshotTab(w, settings,
                            lambda: settings.current_agent, tree=tree)
    _seed_device_snapshot(ds); _pump()
    _save(ds, "device_snapshot", size=(860, 660))
    ds.close()

    # --- Port View ----------------------------------------------------
    from pymibbrowser.ui.port_view_tab import PortViewTab
    pv = PortViewTab(w, settings, lambda: settings.current_agent,
                      tree=tree)
    pv._timer.stop()
    _seed_port_view(pv); _pump()
    _save(pv, "port_view", size=(900, 560))
    pv.close()

    # --- Graph --------------------------------------------------------
    from pymibbrowser.ui.graph_window import GraphTab
    # stop polling before it hits real SNMP
    GraphTab._start = lambda self: None
    gt = GraphTab(w, settings.current_agent,
                   (1, 3, 6, 1, 2, 1, 2, 2, 1, 10, 1),
                   "ifInOctets.1", tree=tree)
    _seed_graph(gt)
    _save(gt, "graph", size=(880, 520))
    gt._timer.stop(); gt.close()

    # --- Network tools ------------------------------------------------
    from pymibbrowser.ui.network_tools import (
        PingDialog, TracerouteDialog, DiscoveryDialog,
    )
    pg = PingDialog("1.1.1.1", w)
    _seed_ping(pg); _pump()
    _save(pg, "ping", size=(720, 440))
    pg.close()

    tr_ = TracerouteDialog("1.1.1.1", w)
    _seed_traceroute(tr_); _pump()
    _save(tr_, "traceroute", size=(780, 440))
    tr_.close()

    dd = DiscoveryDialog(lambda: settings.current_agent,
                          parent=w, settings=settings)
    _seed_discovery(dd); _pump()
    _save(dd, "network_discovery", size=(860, 520))
    dd.close()

    # --- Trap Sender --------------------------------------------------
    from pymibbrowser.ui.tool_dialogs import (
        TrapSenderDialog, AgentSimulatorDialog,
    )
    ts = TrapSenderDialog(tree, settings.current_agent, w)
    _seed_trap_sender(ts); _pump()
    _save(ts, "trap_sender", size=(620, 460))
    ts.close()

    sim = AgentSimulatorDialog(tree, w)
    sim.status.setText("listening on 127.0.0.1:1161 · 42 OIDs loaded")
    _save(sim, "agent_simulator", size=(600, 420))
    sim.close()

    # --- Trap Receiver ------------------------------------------------
    from pymibbrowser.ui.trap_window import TrapReceiverWindow
    tr = TrapReceiverWindow(tree, settings, w)
    _seed_trap_receiver(tr); _pump()
    _save(tr, "trap_receiver", size=(1040, 680))
    tr.close()

    # --- Trap Rule dialog --------------------------------------------
    from pymibbrowser.ui.trap_window import TrapRule, TrapRuleDialog
    rule = TrapRule(name="disk-full",
                    match_oid=".1.3.6.1.4.1.2021.*",
                    allow_ips="192.168.0.0/16",
                    payload_contains="full",
                    action="accept",
                    set_severity="CRITICAL",
                    set_message="Disk full on {src}: {oid}",
                    run_command='notify-send "SNMP" "{msg}"',
                    play_sound="/usr/share/sounds/freedesktop/stereo/bell.oga")
    trd = TrapRuleDialog(rule, w)
    _save(trd, "trap_rule", size=(560, 520))
    trd.close()

    # --- Script Runner -----------------------------------------------
    from pymibbrowser.ui.script_dialog import ScriptDialog, EXAMPLE_PROBE
    sd = ScriptDialog(settings.current_agent, tree, w)
    sd.editor.setPlainText(EXAMPLE_PROBE)
    sd.output.appendPlainText(
        "=== Running script (127.0.0.1:161) ===\n"
        ".1.3.6.1.2.1.1.3.0\tTimeticks\t2 days 17 hours 4 minutes\n"
        "(sleeping 60s)\n"
        ".1.3.6.1.2.1.1.3.0\tTimeticks\t2 days 17 hours 5 minutes\n"
        "=== Script finished ===")
    _save(sd, "script_runner", size=(900, 600))
    sd.close()

    # --- Save walk / Compare -----------------------------------------
    from pymibbrowser.ui.save_walk_dialog import SaveWalkDialog
    sw = SaveWalkDialog(settings.current_agent, tree, w)
    _seed_save_walk(sw); _pump()
    _save(sw, "save_walk", size=(800, 560))
    sw.close()

    from pymibbrowser.ui.compare_dialog import CompareDialog
    cd = CompareDialog(settings, tree, w)
    _seed_compare(cd); _pump()
    _save(cd, "compare_devices", size=(1000, 580))
    cd.close()

    # --- Keyboard shortcuts help (modal info) ------------------------
    # This is a QMessageBox — we render it through show+grab too.
    try:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information = lambda *a, **kw: None  # no-op for demo
    except Exception:
        pass

    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
