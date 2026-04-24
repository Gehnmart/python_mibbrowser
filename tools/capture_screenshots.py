"""Capture UI screenshots for docs/guide/.

Runs headless (QT_QPA_PLATFORM=offscreen) and walks every significant
screen the User Guide references. Keep screenshots reproducible —
one command generates them all.

Usage:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/capture_screenshots.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the package is importable when running from repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QTimer       # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


OUT_DIR = ROOT / "docs" / "guide" / "screenshots"


def _save(widget, name: str, size=None) -> None:
    """Flush the event loop, grab, save."""
    app = QApplication.instance()
    assert app is not None
    if size is not None:
        widget.resize(*size)
    widget.show()
    for _ in range(3):
        app.processEvents()
    pix = widget.grab()
    path = OUT_DIR / f"{name}.png"
    pix.save(str(path), "PNG")
    print(f"  → {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)

    from pymibbrowser.i18n import init_language
    from pymibbrowser.config import (
        AppSettings, Agent, WatchDefinition, PollDefinition, PollVariable,
        compiled_mibs_dir,
    )
    from pymibbrowser.mib_loader import MibTree

    # English for screenshots so the guide text matches.
    init_language("en")
    settings = AppSettings()
    settings.current_agent = Agent(host="127.0.0.1", port=161,
                                    read_community="public")
    settings.saved_agents = [
        Agent(host="router-01", port=161, read_community="public"),
        Agent(host="switch-lobby", port=161, read_community="public"),
    ]
    # Seed a couple of state items so the relevant tabs show content.
    settings.watches = [
        WatchDefinition(name="sysUpTime", oid=".1.3.6.1.2.1.1.3.0",
                         operation="Get", condition_op=">",
                         condition_value="0"),
        WatchDefinition(name="ifInOctets.1", oid=".1.3.6.1.2.1.2.2.1.10.1",
                         operation="Get", condition_op="<",
                         condition_value="100000000"),
    ]
    settings.polls = [
        PollDefinition(
            name="Uptime sweep", interval_s=30,
            agents=["127.0.0.1:161", "router-01:161"],
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
    ]

    tree = MibTree()
    tree.load_compiled(compiled_mibs_dir(), enabled=None)

    from pymibbrowser.ui.main_window import MibBrowserWindow
    w = MibBrowserWindow(tree, settings)
    w.resize(1280, 820)

    # --- main window -------------------------------------------------
    _save(w, "main_window")

    # Expand the tree a bit more so the MIB tree screenshot shows
    # leaves, not just the iso.org.… chain.
    from PyQt6.QtCore import QModelIndex
    idx = w.mib_model.find_index((1, 3, 6, 1, 2, 1, 1))  # system
    if idx.isValid():
        proxy_idx = w.mib_proxy.mapFromSource(idx)
        w.mib_view.expand(proxy_idx)
        w.mib_view.scrollTo(proxy_idx)
        app.processEvents()
    _save(w, "main_window_expanded")

    # --- Preferences --------------------------------------------------
    from pymibbrowser.ui.prefs_dialog import PreferencesDialog
    pd = PreferencesDialog(settings, w)
    pd.resize(720, 560)
    _save(pd, "preferences_general")
    # Switch to SNMP tab
    try:
        # tabs is the top-level QTabWidget in the dialog
        from PyQt6.QtWidgets import QTabWidget
        tabs = pd.findChild(QTabWidget)
        if tabs is not None:
            tabs.setCurrentIndex(1)
            app.processEvents()
            _save(pd, "preferences_snmp")
            tabs.setCurrentIndex(2)  # MIB
            app.processEvents()
            _save(pd, "preferences_mib")
    except Exception as exc:
        print(f"  (prefs tabs: {exc})")
    pd.close()

    # --- MIB Modules --------------------------------------------------
    from pymibbrowser.ui.mib_modules_dialog import MibModulesDialog
    mm = MibModulesDialog(settings, w)
    mm.resize(900, 620)
    # Pre-select a module for a richer detail panel.
    if mm.tbl.rowCount() > 0:
        mm.tbl.selectRow(0)
    app.processEvents()
    _save(mm, "mib_modules")
    mm.close()

    # --- Agents dialog ------------------------------------------------
    from pymibbrowser.ui.agents_dialog import AgentsDialog
    ad = AgentsDialog(settings, w)
    ad.resize(720, 360)
    _save(ad, "manage_agents")
    ad.close()

    # --- Bookmark editor ----------------------------------------------
    # Patch exec so the modal doesn't block.
    from PyQt6.QtWidgets import QDialog
    original_exec = QDialog.exec

    def fake_exec(self) -> int:
        # Capture the dialog when it pops up and store it for saving.
        global _captured_dialog
        _captured_dialog = self
        return 0

    # Bookmark "add" dialog (fed from current toolbar OID)
    w.oid_edit.setText("sysUpTime.0")
    w.op_combo.setCurrentText("Get")
    QDialog.exec = fake_exec
    global _captured_dialog
    _captured_dialog = None
    w._bookmark_current_oid()
    QDialog.exec = original_exec
    if _captured_dialog is not None:
        _captured_dialog.resize(460, 260)
        _save(_captured_dialog, "bookmark_dialog")
        _captured_dialog.close()

    # Manage bookmarks
    QDialog.exec = fake_exec
    _captured_dialog = None
    w._edit_bookmarks()
    QDialog.exec = original_exec
    if _captured_dialog is not None:
        _captured_dialog.resize(640, 400)
        _save(_captured_dialog, "manage_bookmarks")
        _captured_dialog.close()

    # --- Polls dialog -------------------------------------------------
    from pymibbrowser.ui.polls_dialog import PollDialog
    poll_dlg = PollDialog(settings.polls[0], settings, tree=tree, parent=w)
    poll_dlg.resize(620, 520)
    _save(poll_dlg, "create_poll")
    poll_dlg.close()

    # --- Tool dialogs -------------------------------------------------
    # Trap Sender
    try:
        from pymibbrowser.ui.tool_dialogs import TrapSenderDialog
        ts = TrapSenderDialog(tree, settings.current_agent, w)
        ts.resize(620, 460)
        _save(ts, "trap_sender")
        ts.close()
    except Exception as exc:
        print(f"  (trap sender: {exc})")

    # Agent Simulator
    try:
        from pymibbrowser.ui.tool_dialogs import AgentSimulatorDialog
        sim = AgentSimulatorDialog(tree, w)
        sim.resize(600, 420)
        _save(sim, "agent_simulator")
        sim.close()
    except Exception as exc:
        print(f"  (agent simulator: {exc})")

    # --- Trap Receiver ------------------------------------------------
    try:
        from pymibbrowser.ui.trap_window import TrapReceiverWindow
        tr = TrapReceiverWindow(tree, settings, w)
        tr.resize(1000, 640)
        _save(tr, "trap_receiver")
        tr.close()
    except Exception as exc:
        print(f"  (trap receiver: {exc})")

    # --- Network Discovery --------------------------------------------
    try:
        from pymibbrowser.ui.network_tools import DiscoveryDialog
        dd = DiscoveryDialog(lambda: settings.current_agent,
                              parent=w, settings=settings)
        dd.resize(820, 500)
        _save(dd, "network_discovery")
        dd.close()
    except Exception as exc:
        print(f"  (discovery: {exc})")

    # Ping / Traceroute dialogs
    try:
        from pymibbrowser.ui.network_tools import PingDialog, TracerouteDialog
        pg = PingDialog("127.0.0.1", w); pg.resize(640, 360)
        _save(pg, "ping"); pg.close()
        tr_ = TracerouteDialog("1.1.1.1", w); tr_.resize(720, 380)
        _save(tr_, "traceroute"); tr_.close()
    except Exception as exc:
        print(f"  (ping/trace: {exc})")

    # --- Script Runner ------------------------------------------------
    try:
        from pymibbrowser.ui.script_dialog import ScriptDialog, EXAMPLE_PROBE
        sd = ScriptDialog(settings.current_agent, tree, w)
        sd.editor.setPlainText(EXAMPLE_PROBE)
        sd.resize(900, 600)
        _save(sd, "script_runner")
        sd.close()
    except Exception as exc:
        print(f"  (script runner: {exc})")

    # --- Save walk / Compare ------------------------------------------
    try:
        from pymibbrowser.ui.save_walk_dialog import SaveWalkDialog
        sw = SaveWalkDialog(settings.current_agent, tree, w)
        sw.resize(720, 500)
        _save(sw, "save_walk")
        sw.close()
    except Exception as exc:
        print(f"  (save walk: {exc})")
    try:
        from pymibbrowser.ui.compare_dialog import CompareDialog
        cd = CompareDialog(settings, tree, w)
        cd.resize(960, 580)
        _save(cd, "compare_devices")
        cd.close()
    except Exception as exc:
        print(f"  (compare: {exc})")

    w.close()
    return 0


_captured_dialog = None  # populated by the fake_exec closure above

if __name__ == "__main__":
    sys.exit(main())
