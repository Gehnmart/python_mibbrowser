"""Port View — a grid of interface tiles with live bandwidth utilization.

Walks ifTable (ifIndex, ifDescr, ifType, ifSpeed, ifOperStatus,
ifInOctets, ifOutOctets) on a periodic tick. Bandwidth utilization is
computed from the delta of in+out octets over the interval, normalized
against ifSpeed.

Each tile is a clickable QFrame that shows:
  [ ● up/down badge  ifDescr ]
  [ ███████░░░  63% util   ]
  [ Speed · Type          ]

Click a tile → opens a Graph tab on ifInOctets.<ifIndex> (rate mode).

Matches iReasoning Port View (clip_image023.jpg).
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import snmp_ops, workers
from ..config import AppSettings
from ..i18n import _t

# ifTable (RFC 2863) columns.
_IF_DESCR       = (1, 3, 6, 1, 2, 1, 2, 2, 1, 2)
_IF_TYPE        = (1, 3, 6, 1, 2, 1, 2, 2, 1, 3)
_IF_SPEED       = (1, 3, 6, 1, 2, 1, 2, 2, 1, 5)
_IF_OPER_STATUS = (1, 3, 6, 1, 2, 1, 2, 2, 1, 8)
_IF_IN_OCTETS   = (1, 3, 6, 1, 2, 1, 2, 2, 1, 10)
_IF_OUT_OCTETS  = (1, 3, 6, 1, 2, 1, 2, 2, 1, 16)

# ifXTable 64-bit High-Capacity counters (ifHCInOctets / ifHCOutOctets).
# For anything above 100 Mbps — and notably for tun/VPN interfaces that
# routinely overflow the 32-bit ifInOctets every few minutes — these are
# the correct fields to read. We walk them too and prefer them when
# present; fall back to ifInOctets when the agent returns no HC entry
# (older SNMPv1-only agents).
_IF_HC_IN_OCTETS  = (1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 6)
_IF_HC_OUT_OCTETS = (1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 10)
# And the ifXTable's ifHighSpeed (Mbps), used when ifSpeed saturates at
# its 32-bit 4.3 Gbps maximum — a 10G/25G interface is nonsensically
# reported as "4.3 Gbps" by ifSpeed alone.
_IF_HIGH_SPEED    = (1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 15)


def _fmt_bps(v: float) -> str:
    for unit, div in (("Gbps", 1e9), ("Mbps", 1e6), ("Kbps", 1e3)):
        if v >= div:
            return f"{v/div:.1f} {unit}"
    return f"{v:.0f} bps"


class PortTile(QFrame):
    """One interface tile. Owned by PortViewTab; emits `clicked(ifIndex)`
    when the user clicks it so the parent can route to a Graph tab."""

    clicked = pyqtSignal(int)

    def __init__(self, if_index: int, parent=None) -> None:
        super().__init__(parent)
        self.if_index = if_index
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setMinimumWidth(200)
        self.setMinimumHeight(90)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(2)

        head = QHBoxLayout()
        self.badge = QLabel("●")
        self.badge.setStyleSheet("color: #888; font-size: 14pt;")
        self.title = QLabel(f"#{if_index}")
        f = self.title.font(); f.setBold(True); self.title.setFont(f)
        head.addWidget(self.badge)
        head.addWidget(self.title, 1)
        v.addLayout(head)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat("%p%%")
        self.bar.setTextVisible(True)
        v.addWidget(self.bar)

        self.sub = QLabel("")
        self.sub.setStyleSheet("color: #666; font-size: 8pt;")
        v.addWidget(self.sub)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.if_index)
        super().mousePressEvent(ev)

    def update_state(self, descr: str, status: str, util_pct: float | None,
                     speed_bps: float | None, if_type: str,
                     throughput_bps: float | None = None) -> None:
        label = descr or f"#{self.if_index}"
        self.title.setText(f"{label}  (#{self.if_index})")
        # Badge colour reflects ifOperStatus. ifOperStatus is an enum:
        #   1 up, 2 down, 3 testing, 4 unknown, 5 dormant,
        #   6 notPresent, 7 lowerLayerDown
        # pysmi may or may not inline enum names depending on which
        # TC modules are loaded — accept both the symbolic form
        # ("up(1)"/"up"/"down") and the raw integer from the wire.
        st = (status or "").strip().lower()
        is_up = "up" in st or st == "1"
        is_down = ("down" in st or st == "2" or st == "7"
                   or "lowerlayerdown" in st or "notpresent" in st
                   or st == "6")
        if is_up:
            self.badge.setStyleSheet("color: #2a8a2a; font-size: 14pt;")
            self.setToolTip(_t("Interface is up") + f"  ({status})")
        elif is_down:
            self.badge.setStyleSheet("color: #b03030; font-size: 14pt;")
            self.setToolTip(_t("Interface is down") + f"  ({status})")
        else:
            self.badge.setStyleSheet("color: #888; font-size: 14pt;")
            self.setToolTip(status or "")

        if util_pct is None:
            self.bar.setValue(0)
            self.bar.setFormat("—")
        else:
            # Use float in the label so 0.1% traffic on a 1G link is
            # visible; the bar itself is still an int 0..100 (Qt).
            clamped = max(0.0, min(100.0, util_pct))
            self.bar.setValue(round(clamped))
            self.bar.setFormat(f"{clamped:.2f}%")

        sub_parts: list[str] = []
        # Show actual throughput next to link speed whenever we've seen
        # at least one delta tick — "0 bps" for an idle port is useful
        # information, not noise, because it tells the user we're
        # polling and the port really is idle (vs. "we don't know yet").
        if throughput_bps is not None:
            sub_parts.append(_fmt_bps(throughput_bps))
        if speed_bps is not None and speed_bps > 0:
            sub_parts.append(_fmt_bps(speed_bps))
        if if_type:
            sub_parts.append(if_type)
        self.sub.setText(" · ".join(sub_parts))


class PortViewTab(QWidget):
    def __init__(self, parent, settings: AppSettings, current_agent_fn,
                 tree=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._current_agent_fn = current_agent_fn
        self.tree = tree
        self._paused = False
        self._active_threads: list = []

        # Per-ifIndex state: ifDescr/ifType/ifSpeed/ifOperStatus/last
        # (in_octets, out_octets, timestamp) for delta computation.
        self._ifaces: dict[int, dict] = {}
        self._tiles: dict[int, PortTile] = {}

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._refresh()
        self._timer.start(max(1, self._interval_s()) * 1000)

    def _interval_s(self) -> int:
        return max(1, self.interval_edit.value())

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
        tb.addWidget(QLabel(_t("Interval:") + " "))
        self.interval_edit = QSpinBox()
        self.interval_edit.setRange(2, 60)
        self.interval_edit.setValue(5)
        self.interval_edit.setSuffix(" s")
        self.interval_edit.valueChanged.connect(self._on_interval_changed)
        tb.addWidget(self.interval_edit)
        tb.addSeparator()
        agent = self._current_agent_fn()
        self.target_label = QLabel(
            _t("Target") + f": {agent.host}:{agent.port}")
        tb.addWidget(self.target_label)
        v.addWidget(tb)

        # Scrollable grid of tiles — device might have hundreds of
        # interfaces, we don't want them squashed.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        container = QWidget()
        self.grid = QGridLayout(container)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(6)
        self.scroll.setWidget(container)
        v.addWidget(self.scroll, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #666;")
        v.addWidget(self.status)

    def _on_interval_changed(self, v: int) -> None:
        if not self._paused:
            self._timer.start(max(1, v) * 1000)

    def _toggle_pause(self, checked: bool) -> None:
        self._paused = checked
        self.pause_btn.setText(
            _t("▶ Resume") if checked else _t("⏸ Pause"))
        if checked:
            self._timer.stop()
        else:
            self._timer.start(self._interval_s() * 1000)
            self._refresh()

    # --- refresh ------------------------------------------------------

    def _refresh(self) -> None:
        if self._paused:
            return
        workers.prune_threads(self._active_threads)
        agent = self._current_agent_fn()
        self.target_label.setText(
            _t("Target") + f": {agent.host}:{agent.port}")
        # Walk ifTable + ifXTable in parallel. HC counters are preferred
        # for rate calculation; regular are a fallback when the agent
        # doesn't publish ifXTable (SNMPv1-only or a stripped MIB view).
        self._pending = 9
        self._collected: dict[tuple[int, ...], dict] = {}
        self._walk_col(agent, "descr",     _IF_DESCR)
        self._walk_col(agent, "type",      _IF_TYPE)
        self._walk_col(agent, "speed",     _IF_SPEED)
        self._walk_col(agent, "high_speed", _IF_HIGH_SPEED)
        self._walk_col(agent, "status",    _IF_OPER_STATUS)
        self._walk_col(agent, "in_oct",    _IF_IN_OCTETS)
        self._walk_col(agent, "out_oct",   _IF_OUT_OCTETS)
        self._walk_col(agent, "hc_in",     _IF_HC_IN_OCTETS)
        self._walk_col(agent, "hc_out",    _IF_HC_OUT_OCTETS)
        self.status.setText(_t("Refreshing…"))

    def _walk_col(self, agent, key: str, coid: tuple[int, ...]) -> None:
        def on_prog(vb):
            suffix = tuple(vb.oid[len(coid):])
            # Most rows have a single-index suffix (the ifIndex). Reject
            # unexpected shapes defensively.
            if len(suffix) != 1:
                return
            self._collected.setdefault(suffix, {})[key] = vb.display_value
        def on_done(_vbs):
            self._pending -= 1
            if self._pending == 0:
                self._render()
        def on_fail(_m):
            self._pending -= 1
            if self._pending == 0:
                self._render()
        t, _w = workers.run_op(
            self, snmp_ops.op_walk, on_done, on_fail, on_prog, agent, coid)
        self._active_threads.append(t)

    def _render(self) -> None:
        now = time.monotonic()
        current_indexes = []
        for (idx,), data in sorted(self._collected.items()):
            current_indexes.append(idx)
            speed_bps = _parse_float(data.get("speed"))
            # Prefer ifHighSpeed (Mbps) when present and larger —
            # matches what a 10G/25G interface will actually serve.
            hs_mbps = _parse_float(data.get("high_speed"))
            if hs_mbps is not None and hs_mbps > 0:
                hs_bps = hs_mbps * 1_000_000
                if speed_bps is None or hs_bps > speed_bps:
                    speed_bps = hs_bps
            # Prefer 64-bit HC counters; fall back to 32-bit ifInOctets.
            hc_in = _parse_int(data.get("hc_in"))
            hc_out = _parse_int(data.get("hc_out"))
            in_oct = hc_in if hc_in is not None else _parse_int(data.get("in_oct"))
            out_oct = hc_out if hc_out is not None else _parse_int(data.get("out_oct"))
            prev = self._ifaces.get(idx, {})
            prev_in = prev.get("in_oct")
            prev_out = prev.get("out_oct")
            prev_t = prev.get("t")
            util_pct: float | None = None
            bits_per_s: float | None = None
            if (prev_in is not None and prev_out is not None
                    and prev_t is not None and in_oct is not None
                    and out_oct is not None):
                dt = now - prev_t
                if dt > 0:
                    d_in = in_oct - prev_in
                    d_out = out_oct - prev_out
                    # Counter wrap / reset → drop this tick, keep prev
                    # for next round so we don't show a spike.
                    if d_in < 0 or d_out < 0:
                        bits_per_s = None
                    else:
                        bits_per_s = (d_in + d_out) * 8 / dt
                        if speed_bps and speed_bps > 0:
                            util_pct = bits_per_s / speed_bps * 100

            tile = self._tiles.get(idx)
            if tile is None:
                tile = PortTile(idx)
                tile.clicked.connect(self._on_tile_click)
                self._tiles[idx] = tile

            tile.update_state(
                descr=data.get("descr", ""),
                status=data.get("status", ""),
                util_pct=util_pct,
                speed_bps=speed_bps,
                if_type=data.get("type", ""),
                throughput_bps=bits_per_s,
            )
            # Diagnostic tooltip — shows which counter source won and
            # the raw octet totals, so the user can tell at a glance
            # whether the agent is returning zeros (snmpd misconfig on
            # tun/VPN interfaces) vs us miscalculating.
            src_in = "ifHCInOctets" if hc_in is not None else "ifInOctets"
            src_out = "ifHCOutOctets" if hc_out is not None else "ifOutOctets"
            tile.bar.setToolTip(
                f"{src_in}: {in_oct}\n"
                f"{src_out}: {out_oct}\n"
                f"speed: {speed_bps}"
            )

            # Remember for next delta — but ONLY if we have numeric
            # counter values. Overwriting with None wipes the previous
            # reading, causing util_pct to never recover on the next
            # tick. That was the "always 0 for non-lo" bug.
            if in_oct is not None and out_oct is not None:
                self._ifaces[idx] = {
                    "in_oct": in_oct, "out_oct": out_oct, "t": now,
                }

        # Drop tiles for interfaces that vanished.
        stale = set(self._tiles) - set(current_indexes)
        for idx in stale:
            self._tiles[idx].setParent(None)
            del self._tiles[idx]

        self._relayout()
        self.status.setText(
            _t("{n} interface(s) · updated every {s}s").format(
                n=len(current_indexes), s=self._interval_s()))

    def _relayout(self) -> None:
        # Compute column count from available width; fall back to 4.
        width = max(240, self.scroll.viewport().width())
        cols = max(1, width // 220)
        # Clear grid.
        while self.grid.count():
            self.grid.takeAt(0)
        for i, idx in enumerate(sorted(self._tiles)):
            r, c = divmod(i, cols)
            self.grid.addWidget(self._tiles[idx], r, c)
        # Push everything to the top.
        self.grid.setRowStretch(self.grid.rowCount(), 1)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._relayout()

    def _on_tile_click(self, if_index: int) -> None:
        """Open a Graph tab on this port's ifInOctets in rate mode.

        ifInOctets is a cumulative Counter32 — plotting it raw gives a
        monotonically increasing line. Rate mode (delta / dt) shows the
        actual throughput, which is what the user cares about when
        clicking through from a bandwidth tile."""
        oid = "." + ".".join(str(x) for x in _IF_IN_OCTETS) + f".{if_index}"
        p = self.parent()
        while p is not None and not hasattr(p, "oid_edit"):
            p = p.parent() if hasattr(p, "parent") else None
        if p is None:
            return
        p.oid_edit.setText(oid)
        if hasattr(p, "_open_graph"):
            p._open_graph(rate=True)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)


def _parse_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).split()[0])
    except (ValueError, IndexError):
        return None


def _parse_int(v) -> int | None:
    if v is None:
        return None
    try:
        # display_value for counters often ends up like "123456"; sometimes
        # Python's int() is fussier than needed.
        s = str(v).strip().split()[0]
        return int(s)
    except (ValueError, IndexError):
        return None
