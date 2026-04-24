"""Built-in ping / traceroute / network discovery dialogs.

Ping and traceroute just shell out to the system tools and stream
stdout into a scrollable output pane via a QThread. Keeps the UI
responsive and bypasses CAP_NET_RAW (no root needed).

Network Discovery does its own async sweep: concurrent-threaded
ICMP ping (delegates to the system `ping`), then an SNMP GET against
every live host using the main window's current agent credentials
for sysName / sysDescr — gives you a usable inventory table in a
few seconds.
"""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import snmp_ops, workers
from ..config import Agent
from ..i18n import _t

# ---------------------------------------------------------------------------
# Subprocess-streaming worker — used by both ping and traceroute
# ---------------------------------------------------------------------------

class _StreamWorker(QObject):
    line = pyqtSignal(str)
    done = pyqtSignal(int)      # exit code
    failed = pyqtSignal(str)

    def __init__(self, argv: list[str]) -> None:
        super().__init__()
        self._argv = argv
        self._proc: subprocess.Popen | None = None
        self._cancel = False

    def run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self._argv, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1)
        except FileNotFoundError:
            self.failed.emit(f"{self._argv[0]}: command not found")
            return
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        try:
            assert self._proc.stdout is not None
            for raw in self._proc.stdout:
                if self._cancel:
                    self._proc.terminate()
                    break
                self.line.emit(raw.rstrip("\n"))
            code = self._proc.wait()
            self.done.emit(code)
        except Exception as exc:
            self.failed.emit(str(exc))

    def cancel(self) -> None:
        self._cancel = True
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass


def _mono() -> QFont:
    f = QFont("Monospace"); f.setStyleHint(QFont.StyleHint.TypeWriter)
    f.setPointSize(10)
    return f


# ---------------------------------------------------------------------------
# _StreamDialog — shared skeleton for Ping / Traceroute.
#
# Subclasses implement _build_input_row (the host/count widgets) and
# _build_argv (translate current inputs to a subprocess argv list). The
# base class handles spawning the QThread, streaming stdout into the
# output pane, Stop behaviour and orderly shutdown.
# ---------------------------------------------------------------------------

class _StreamDialog(QDialog):
    run_button_label = "Run ▶"       # overridden by subclasses
    default_title = "Stream"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t(self.default_title))
        self.resize(720, 440)
        self._thread: QThread | None = None
        self._worker: _StreamWorker | None = None

        v = QVBoxLayout(self)
        row = QHBoxLayout()
        # Subclasses fill the left side of this row with their inputs.
        self._build_input_row(row)
        self.run_b = QPushButton(_t(self.run_button_label))
        self.run_b.setDefault(True)
        self.run_b.clicked.connect(self._run)
        row.addWidget(self.run_b)
        self.stop_b = QPushButton(_t("Stop"))
        self.stop_b.setEnabled(False)
        self.stop_b.clicked.connect(self._stop)
        row.addWidget(self.stop_b)
        v.addLayout(row)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(_mono())
        self.output.setMaximumBlockCount(10000)
        v.addWidget(self.output, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #666;")
        v.addWidget(self.status)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.reject)
        btns.button(QDialogButtonBox.StandardButton.Close).setText(_t("Close"))
        v.addWidget(btns)

    # --- hooks to override ------------------------------------------

    def _build_input_row(self, row: QHBoxLayout) -> None:
        """Populate the left half of the toolbar row with host/count/etc."""
        raise NotImplementedError

    def _build_argv(self) -> list[str] | None:
        """Translate current inputs to argv. Return None to cancel (e.g.
        no host entered, or the required binary isn't installed — in
        which case the subclass should also print an explanation)."""
        raise NotImplementedError

    # --- run / stop / teardown --------------------------------------

    def _run(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        argv = self._build_argv()
        if not argv:
            return
        self.output.appendPlainText("$ " + " ".join(argv))
        self.run_b.setEnabled(False)
        self.stop_b.setEnabled(True)
        self.status.setText(_t("Running…"))

        self._thread = QThread(self)
        self._worker = _StreamWorker(argv)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.line.connect(self.output.appendPlainText)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.done.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _on_done(self, code: int) -> None:
        self.run_b.setEnabled(True)
        self.stop_b.setEnabled(False)
        self.status.setText(
            _t("Finished (exit {code})").format(code=code))

    def _on_failed(self, msg: str) -> None:
        self.run_b.setEnabled(True)
        self.stop_b.setEnabled(False)
        self.output.appendPlainText("!!! " + msg)
        self.status.setText(_t("Failed"))

    def closeEvent(self, ev) -> None:
        self._stop()
        if self._thread is not None:
            workers.wait_if_running(self._thread, 500)
        super().closeEvent(ev)


# ---------------------------------------------------------------------------
# Ping dialog
# ---------------------------------------------------------------------------

class PingDialog(_StreamDialog):
    run_button_label = "Ping ▶"
    default_title = "Ping"

    def __init__(self, default_host: str = "", parent=None) -> None:
        self._default_host = default_host or "127.0.0.1"
        super().__init__(parent)

    def _build_input_row(self, row: QHBoxLayout) -> None:
        row.addWidget(QLabel(_t("Host") + ":"))
        self.host_edit = QLineEdit(self._default_host)
        row.addWidget(self.host_edit, 1)
        row.addWidget(QLabel(_t("Count") + ":"))
        self.count_edit = QSpinBox()
        self.count_edit.setRange(1, 1000)
        self.count_edit.setValue(5)
        row.addWidget(self.count_edit)

    def _build_argv(self) -> list[str] | None:
        host = self.host_edit.text().strip()
        if not host:
            return None
        count = int(self.count_edit.value())
        # -n disables DNS reverse lookup on Linux; -c sets count. macOS'
        # ping accepts the same flags; Windows' ping uses -n for count
        # and has no -c. We target Linux-first so keep this short.
        if sys.platform.startswith(("linux", "darwin")):
            return ["ping", "-n", "-c", str(count), host]
        return ["ping", "-n", str(count), host]


# ---------------------------------------------------------------------------
# Traceroute dialog
# ---------------------------------------------------------------------------

class TracerouteDialog(_StreamDialog):
    run_button_label = "Trace ▶"
    default_title = "Traceroute"

    def __init__(self, default_host: str = "", parent=None) -> None:
        self._default_host = default_host or ""
        super().__init__(parent)

    def _build_input_row(self, row: QHBoxLayout) -> None:
        row.addWidget(QLabel(_t("Host") + ":"))
        self.host_edit = QLineEdit(self._default_host)
        row.addWidget(self.host_edit, 1)
        row.addWidget(QLabel(_t("Max hops") + ":"))
        self.hops_edit = QSpinBox()
        self.hops_edit.setRange(1, 64)
        self.hops_edit.setValue(30)
        row.addWidget(self.hops_edit)

    def _build_argv(self) -> list[str] | None:
        host = self.host_edit.text().strip()
        if not host:
            return None
        hops = int(self.hops_edit.value())
        if shutil.which("tracepath"):
            # tracepath doesn't need root and handles MTU discovery.
            return ["tracepath", "-n", host]
        if shutil.which("traceroute"):
            return ["traceroute", "-n", "-m", str(hops), host]
        self.output.appendPlainText(
            "!!! " + _t("Neither tracepath nor traceroute is "
                        "installed. On Debian/Ubuntu: "
                        "sudo apt install iputils-tracepath "
                        "or traceroute."))
        return None


# ---------------------------------------------------------------------------
# Network discovery
# ---------------------------------------------------------------------------

# The discovery runs in its own thread because a /24 sweep takes a few
# seconds of wall clock: ~256 ICMP pings + SNMP GET on each responder.

SYS_NAME_OID = (1, 3, 6, 1, 2, 1, 1, 5, 0)
SYS_DESCR_OID = (1, 3, 6, 1, 2, 1, 1, 1, 0)


def _ping_once(host: str, timeout_ms: int = 500) -> float | None:
    """Return RTT in ms or None. One packet, short timeout."""
    try:
        # -W is seconds on Linux, milliseconds / msec on macOS. We use
        # a generous integer-second timeout to stay portable.
        secs = max(1, (timeout_ms + 999) // 1000)
        r = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", str(secs), host],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=secs + 2)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if "time=" in line:
            try:
                after = line.split("time=", 1)[1]
                num = after.split()[0]
                return float(num)
            except Exception:
                continue
    return 0.0   # responded but no rtt we could parse


class _DiscoveryWorker(QObject):
    progress = pyqtSignal(int, int)           # done, total
    result = pyqtSignal(str, float, str, str) # ip, rtt_ms, sysName, sysDescr
    done = pyqtSignal(int)                    # live count
    failed = pyqtSignal(str)

    def __init__(self, cidr: str, snmp_probe: bool, agent: Agent) -> None:
        super().__init__()
        self._cidr = cidr
        self._snmp = snmp_probe
        self._agent = agent
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            net = ipaddress.ip_network(self._cidr, strict=False)
        except ValueError as exc:
            self.failed.emit(f"invalid CIDR: {exc}")
            return
        if net.num_addresses > 4096:
            self.failed.emit(
                f"{net.num_addresses} hosts — too large (max 4096). "
                "Narrow the prefix.")
            return

        hosts = [str(h) for h in (net.hosts() if net.prefixlen < 31
                                   else net)]
        total = len(hosts)
        live: list[str] = []
        with ThreadPoolExecutor(max_workers=64) as ex:
            futures = {ex.submit(_ping_once, h): h for h in hosts}
            for i, fut in enumerate(as_completed(futures), start=1):
                if self._cancel:
                    break
                h = futures[fut]
                try:
                    rtt = fut.result()
                except Exception:
                    rtt = None
                self.progress.emit(i, total)
                if rtt is None:
                    continue
                live.append(h)
                sysname, sysdescr = "", ""
                if self._snmp:
                    sysname, sysdescr = self._probe_snmp(h)
                self.result.emit(h, rtt, sysname, sysdescr)

        self.done.emit(len(live))

    def _probe_snmp(self, host: str) -> tuple[str, str]:
        ag = Agent(**vars(self._agent))
        ag.host = host
        ag.timeout_s = min(self._agent.timeout_s, 1.5)
        ag.retries = 0
        try:
            vbs = snmp_ops.op_get(ag, [SYS_NAME_OID, SYS_DESCR_OID])
        except Exception:
            return "", ""
        name, desc = "", ""
        for vb in vbs:
            if tuple(vb.oid) == SYS_NAME_OID and vb.display_value:
                name = str(vb.display_value)
            elif tuple(vb.oid) == SYS_DESCR_OID and vb.display_value:
                desc = str(vb.display_value)
        return name, desc


class DiscoveryDialog(QDialog):
    def __init__(self, current_agent_fn, parent=None, settings=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Network Discovery"))
        self.resize(820, 520)
        self._current_agent_fn = current_agent_fn
        self._settings = settings        # optional — needed for Add-to-saved
        self._thread: QThread | None = None
        self._worker: _DiscoveryWorker | None = None

        v = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel(_t("Subnet (CIDR)") + ":"))
        self.cidr_edit = QLineEdit("192.168.1.0/24")
        row.addWidget(self.cidr_edit, 1)
        from PyQt6.QtWidgets import QCheckBox
        self.snmp_chk = QCheckBox(_t("Probe SNMP (sysName / sysDescr)"))
        self.snmp_chk.setChecked(True)
        row.addWidget(self.snmp_chk)
        self.run_b = QPushButton(_t("Discover ▶"))
        self.run_b.setDefault(True)
        self.run_b.clicked.connect(self._run)
        row.addWidget(self.run_b)
        self.stop_b = QPushButton(_t("Stop"))
        self.stop_b.setEnabled(False)
        self.stop_b.clicked.connect(self._stop)
        row.addWidget(self.stop_b)
        v.addLayout(row)

        self.bar = QProgressBar()
        self.bar.setValue(0)
        v.addWidget(self.bar)

        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(
            ["", _t("IP"), _t("RTT (ms)"), "sysName", "sysDescr"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tbl, 1)

        btm = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color: #666;")
        btm.addWidget(self.status, 1)
        add_b = QPushButton(_t("Add selected to agents"))
        add_b.setEnabled(self._settings is not None)
        add_b.setToolTip(_t(
            "Create a saved-agent entry for each ticked row using your "
            "current community/version defaults. Duplicates (same "
            "host:port) are skipped."))
        add_b.clicked.connect(self._add_selected_to_agents)
        btm.addWidget(add_b)
        exp_b = QPushButton(_t("Export CSV"))
        exp_b.clicked.connect(self._export_csv)
        btm.addWidget(exp_b)
        close_b = QPushButton(_t("Close"))
        close_b.clicked.connect(self.reject)
        btm.addWidget(close_b)
        v.addLayout(btm)

    def _run(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        cidr = self.cidr_edit.text().strip()
        if not cidr:
            return
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            QMessageBox.warning(self, _t("Network Discovery"), str(exc))
            return
        self.tbl.setRowCount(0)
        self.bar.setValue(0)
        self.bar.setMaximum(max(1, net.num_addresses))
        self.status.setText(_t("Sweeping…"))
        self.run_b.setEnabled(False)
        self.stop_b.setEnabled(True)
        self._thread = QThread(self)
        self._worker = _DiscoveryWorker(
            cidr, self.snmp_chk.isChecked(), self._current_agent_fn())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.result.connect(self._on_result)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.done.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _on_progress(self, done: int, total: int) -> None:
        self.bar.setMaximum(total)
        self.bar.setValue(done)
        self.status.setText(
            _t("Scanned {done} / {total}").format(done=done, total=total))

    def _on_result(self, ip: str, rtt: float, name: str, desc: str) -> None:
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        # Column 0: checkbox — flags this row for the "Add selected" action.
        chk_item = QTableWidgetItem()
        chk_item.setFlags(chk_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        chk_item.setCheckState(Qt.CheckState.Unchecked)
        self.tbl.setItem(r, 0, chk_item)
        self.tbl.setItem(r, 1, QTableWidgetItem(ip))
        rtt_item = QTableWidgetItem(f"{rtt:.1f}" if rtt else "<1")
        rtt_item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.tbl.setItem(r, 2, rtt_item)
        self.tbl.setItem(r, 3, QTableWidgetItem(name))
        self.tbl.setItem(r, 4, QTableWidgetItem(desc))

    def _on_done(self, live: int) -> None:
        self.run_b.setEnabled(True)
        self.stop_b.setEnabled(False)
        self.status.setText(
            _t("Found {live} live host(s)").format(live=live))

    def _on_failed(self, msg: str) -> None:
        self.run_b.setEnabled(True)
        self.stop_b.setEnabled(False)
        self.status.setText(msg)
        QMessageBox.warning(self, _t("Network Discovery"), msg)

    def _export_csv(self) -> None:
        import csv
        path, _sel = QFileDialog.getSaveFileName(
            self, _t("Export CSV"), "discovery.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["IP", "RTT_ms", "sysName", "sysDescr"])
            for r in range(self.tbl.rowCount()):
                # Columns 1..4 (skip the checkbox).
                w.writerow([self.tbl.item(r, c).text() if self.tbl.item(r, c)
                            else "" for c in range(1, 5)])

    def _add_selected_to_agents(self) -> None:
        if self._settings is None:
            return
        existing = {(a.host, a.port) for a in self._settings.saved_agents}
        existing.add((self._settings.current_agent.host,
                       self._settings.current_agent.port))
        added = 0; skipped = 0
        template = self._settings.default_agent
        for r in range(self.tbl.rowCount()):
            chk = self.tbl.item(r, 0)
            if chk is None or chk.checkState() != Qt.CheckState.Checked:
                continue
            ip = self.tbl.item(r, 1).text().strip()
            if not ip:
                continue
            key = (ip, template.port)
            if key in existing:
                skipped += 1
                continue
            new = Agent(**vars(template))
            new.host = ip
            self._settings.saved_agents.append(new)
            existing.add(key)
            added += 1
        if added:
            self._settings.save()
        QMessageBox.information(
            self, _t("Network Discovery"),
            _t("Added {added} agent(s), skipped {skipped} duplicate(s).")
            .format(added=added, skipped=skipped))

    def closeEvent(self, ev) -> None:
        self._stop()
        if self._thread is not None:
            workers.wait_if_running(self._thread, 800)
        super().closeEvent(ev)
