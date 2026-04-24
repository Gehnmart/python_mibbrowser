"""
MIB Modules management dialog.

Lists every compiled MIB in ~/.local/share/pymibbrowser/compiled_mibs/ with
an Enabled checkbox. Saving persists `settings.enabled_mibs` and the caller
reloads the tree so only those modules populate it.

Also supports Unload — deletes the compiled .json (and optional source).
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHeaderView,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout,
)

from .. import config
from ..i18n import _t
from ..mib_loader import STUB_MIBS


# Short blurbs for pysmi's built-in stub modules so clicking through to them
# from "Imports from" actually shows something.
STUB_INFO = {
    "SNMPv2-SMI":  "Core SMI types and macros (MODULE-IDENTITY, OBJECT-TYPE, "
                   "Integer32, IpAddress, TimeTicks, Counter32/64, Gauge32, "
                   "Unsigned32, …). Referenced by virtually every MIB. "
                   "Built into pysmi/pysnmp — no external .mib file.",
    "SNMPv2-TC":   "Standard textual conventions: DisplayString, PhysAddress, "
                   "MacAddress, TruthValue, RowStatus, TimeStamp, DateAndTime, "
                   "TAddress, AutonomousType, InstancePointer, VariablePointer, "
                   "RowPointer, StorageType, TDomain, … Built into pysmi.",
    "SNMPv2-CONF": "Macros used in compliance / group definitions: "
                   "MODULE-COMPLIANCE, OBJECT-GROUP, NOTIFICATION-GROUP, "
                   "AGENT-CAPABILITIES. Built into pysmi.",
    "SNMPv2-MIB":  "RFC 3418 SNMPv2 entities MIB: sysDescr, sysObjectID, "
                   "sysUpTime, sysContact, sysName, sysLocation, sysServices, "
                   "sysORTable, snmpTraps, snmpMIB. Built-in.",
    "RFC1155-SMI": "SMIv1 core types and macros (Gauge, Counter, TimeTicks, "
                   "Opaque, NetworkAddress, IpAddress, INTEGER primitive). "
                   "Built-in for SMIv1 MIB support.",
    "RFC1213-MIB": "MIB-II — the baseline standard MIB for TCP/IP stacks "
                   "(system, interfaces, at, ip, icmp, tcp, udp, egp, "
                   "transmission, snmp). Built-in.",
    "RFC-1212":    "SMIv1 OBJECT-TYPE macro. Built-in.",
    "RFC-1215":    "SMIv1 TRAP-TYPE macro. Built-in.",
    "ASN1":        "ASN.1 primitives (INTEGER, OCTET STRING, OBJECT IDENTIFIER, "
                   "NULL, CHOICE). Built-in.",
    "ASN1-ENUMERATION":  "ASN.1 ENUMERATED helper. Built-in.",
    "ASN1-REFINEMENT":   "ASN.1 sub-typing constraints. Built-in.",
    "INET-ADDRESS-MIB":  "RFC 4001 Internet address textual conventions: "
                         "InetAddressType, InetAddress, InetPortNumber, "
                         "InetAddressPrefixLength, …",
}


# Core "framework" modules we keep enabled by default even when user narrows
# the view — otherwise TCs like DisplayString / TimeStamp / etc. used by
# everyone else still resolve. User can still uncheck them manually.
BASE_MIBS = (
    "SNMPv2-SMI", "SNMPv2-TC", "SNMPv2-CONF", "SNMPv2-MIB",
    "RFC1213-MIB", "RFC1155-SMI", "RFC-1212", "RFC-1215",
    "INET-ADDRESS-MIB", "SNMP-FRAMEWORK-MIB", "IANAifType-MIB",
)


class MibModulesDialog(QDialog):
    def __init__(self, settings: config.AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("MIB Modules"))
        self.resize(760, 680)
        self.settings = settings
        self.changed = False
        self._cache: dict[str, dict] = {}   # module name → parsed JSON
        # Reverse index "module → set(modules that import from it)". Built
        # lazily on first selection.
        self._dependents: dict[str, set[str]] | None = None

        v = QVBoxLayout(self)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(_t("Filter…"))
        self.filter_edit.textChanged.connect(self._apply_filter)
        v.addWidget(self.filter_edit)

        split = QSplitter(Qt.Orientation.Vertical)

        self.tbl = QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels(
            (_t("Enabled"), _t("Module"), _t("Size")))
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.horizontalHeader().setStretchLastSection(False)
        self.tbl.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setEditTriggers(self.tbl.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(self.tbl.SelectionBehavior.SelectRows)
        self.tbl.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self.tbl)

        self.info = QTextBrowser()
        self.info.setOpenExternalLinks(False)
        self.info.anchorClicked.connect(self._on_anchor)
        split.addWidget(self.info)
        split.setSizes([420, 220])

        v.addWidget(split, 1)

        # Quick presets
        hb = QHBoxLayout()
        all_b = QPushButton(_t("All"))
        all_b.clicked.connect(lambda: self._set_all(True))
        hb.addWidget(all_b)
        none_b = QPushButton(_t("None"))
        none_b.clicked.connect(lambda: self._set_all(False))
        hb.addWidget(none_b)
        inv_b = QPushButton(_t("Invert"))
        inv_b.clicked.connect(self._invert)
        hb.addWidget(inv_b)
        only_vendor_b = QPushButton(_t("Only vendor (no RFC/SNMPv2)"))
        only_vendor_b.clicked.connect(self._only_vendor)
        hb.addWidget(only_vendor_b)
        hb.addStretch()
        unload_b = QPushButton(_t("Unload selected"))
        unload_b.clicked.connect(self._unload_selected)
        hb.addWidget(unload_b)
        v.addLayout(hb)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

        self._populate()

    # ------------------------------------------------------------------

    def _populate(self) -> None:
        d = config.compiled_mibs_dir()
        mibs: list[tuple[str, int]] = []
        for jp in sorted(d.glob("*.json")):
            try:
                size = jp.stat().st_size
            except OSError:
                size = 0
            mibs.append((jp.stem, size))

        enabled = self.settings.enabled_mibs
        wanted = set(enabled) if enabled is not None else None

        bold = QFont(); bold.setBold(True)
        self.tbl.setRowCount(len(mibs))
        for r, (name, size) in enumerate(mibs):
            chk = QCheckBox()
            if wanted is None or name in wanted:
                chk.setChecked(True)
            chk.stateChanged.connect(lambda _=0: self._mark_changed())
            holder = QTableWidgetItem()
            self.tbl.setItem(r, 0, holder)
            self.tbl.setCellWidget(r, 0, chk)

            name_item = QTableWidgetItem(name)
            if name in BASE_MIBS:
                name_item.setFont(bold)
                name_item.setToolTip(_t("Framework module — used by many others."))
            self.tbl.setItem(r, 1, name_item)

            size_item = QTableWidgetItem(f"{size // 1024} KB" if size >= 1024
                                          else f"{size} B")
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tbl.setItem(r, 2, size_item)

    def _apply_filter(self, text: str) -> None:
        text = text.lower()
        for r in range(self.tbl.rowCount()):
            name = self.tbl.item(r, 1).text().lower()
            self.tbl.setRowHidden(r, bool(text) and text not in name)

    def _mark_changed(self) -> None:
        self.changed = True

    def _set_all(self, value: bool) -> None:
        for r in range(self.tbl.rowCount()):
            if self.tbl.isRowHidden(r):
                continue
            cb = self.tbl.cellWidget(r, 0)
            if isinstance(cb, QCheckBox):
                cb.setChecked(value)
        self._mark_changed()

    def _invert(self) -> None:
        for r in range(self.tbl.rowCount()):
            if self.tbl.isRowHidden(r):
                continue
            cb = self.tbl.cellWidget(r, 0)
            if isinstance(cb, QCheckBox):
                cb.setChecked(not cb.isChecked())
        self._mark_changed()

    def _only_vendor(self) -> None:
        """Uncheck RFC* / SNMP* / *-TC / *-CONF modules, keep everything else.
        Keeps base framework modules (so TCs resolve)."""
        for r in range(self.tbl.rowCount()):
            name = self.tbl.item(r, 1).text()
            cb = self.tbl.cellWidget(r, 0)
            if not isinstance(cb, QCheckBox):
                continue
            if name in BASE_MIBS:
                cb.setChecked(True)
                continue
            is_standard = (name.startswith(("RFC", "IANA",
                                             "SNMPv2-", "SNMP-"))
                           or name in ("IF-MIB", "IP-MIB", "TCP-MIB",
                                       "UDP-MIB", "HOST-RESOURCES-MIB",
                                       "DISMAN-EVENT-MIB",
                                       "NET-SNMP-MIB",
                                       "NET-SNMP-AGENT-MIB",
                                       "UCD-SNMP-MIB"))
            cb.setChecked(not is_standard)
        self._mark_changed()

    def _unload_selected(self) -> None:
        rows = {idx.row() for idx in self.tbl.selectedIndexes()}
        if not rows:
            return
        names = [self.tbl.item(r, 1).text() for r in sorted(rows)]
        bases = [n for n in names if n in BASE_MIBS]
        confirm = _t("Delete compiled files for these modules?") \
                  + "\n\n" + ", ".join(names)
        if bases:
            confirm += "\n\n" + _t(
                "Warning: unloading framework modules ({}) will break "
                "others.").format(", ".join(bases))
        r = QMessageBox.question(self, _t("Unload selected"), confirm)
        if r != QMessageBox.StandardButton.Yes:
            return
        d = config.compiled_mibs_dir()
        for name in names:
            (d / f"{name}.json").unlink(missing_ok=True)
        self._populate()
        self._mark_changed()

    def _render_stub_info(self, name: str) -> str:
        """Info panel for a module that's not in the compiled cache —
        usually a framework stub (SNMPv2-SMI etc.) or a missing dep."""
        if self._dependents is None:
            self._dependents = self._build_dependents_index()
        deps = self._dependents.get(name) or set()

        blurb = STUB_INFO.get(name)
        parts = [f'<h3>{name}</h3>']
        if name in STUB_MIBS:
            parts.append(f'<p><i>{_t("Built-in framework module")}.</i></p>')
        else:
            msg = _t("Not in the compiled cache. Use File → Load MIB to add it.")
            parts.append(f'<p><i>{msg}</i></p>')
        if blurb:
            parts.append(f'<p>{blurb}</p>')
        if deps:
            parts.append(f'<p><b>{_t("Imported by")}</b> ({len(deps)}):</p><ul>')
            for m in sorted(deps):
                parts.append(f'<li><a href="{m}">{m}</a></li>')
            parts.append("</ul>")
        return "".join(parts)

    # --- Module-info panel --------------------------------------------

    def _load_module(self, name: str) -> dict:
        if name in self._cache:
            return self._cache[name]
        path = config.compiled_mibs_dir() / f"{name}.json"
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
        self._cache[name] = data
        return data

    def _build_dependents_index(self) -> dict[str, set[str]]:
        idx: dict[str, set[str]] = {}
        for p in config.compiled_mibs_dir().glob("*.json"):
            try:
                data = json.loads(p.read_text())
            except Exception:
                continue
            imports = data.get("imports") or {}
            for dep_mod in imports:
                idx.setdefault(dep_mod, set()).add(p.stem)
        return idx

    def _on_select(self) -> None:
        rows = {i.row() for i in self.tbl.selectedIndexes()}
        if not rows:
            self.info.clear()
            return
        name = self.tbl.item(min(rows), 1).text()
        self.info.setHtml(self._render_info(name))

    def _on_anchor(self, url) -> None:
        """Click on a dependency/dependent link → jump to that module row.
        If the target is hidden by the current filter, clear the filter
        first so the row is reachable."""
        target = url.toString()
        # Find target row regardless of current filter visibility.
        target_row = -1
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r, 1).text() == target:
                target_row = r
                break
        if target_row < 0:
            self.info.setHtml(self._render_stub_info(target))
            return
        if self.tbl.isRowHidden(target_row):
            self.filter_edit.blockSignals(True)
            self.filter_edit.clear()
            self.filter_edit.blockSignals(False)
            for r in range(self.tbl.rowCount()):
                self.tbl.setRowHidden(r, False)
        self.tbl.selectRow(target_row)
        self.tbl.scrollToItem(self.tbl.item(target_row, 1))

    @staticmethod
    def _fmt_timestamp(raw: str) -> str:
        """Convert ASN.1 timestamp '202208290000Z' → '2022-08-29 00:00 UTC'."""
        if not raw or not raw.endswith("Z") or len(raw) < 13:
            return raw
        s = raw[:-1]
        try:
            return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]} UTC"
        except Exception:
            return raw

    def _render_info(self, name: str) -> str:
        data = self._load_module(name)
        if not data:
            return f"<i>Failed to read {name}.json.</i>"

        mi = None
        for k, v in data.items():
            if isinstance(v, dict) and v.get("class") == "moduleidentity":
                mi = v
                break

        # Size + summary strip at top
        path = config.compiled_mibs_dir() / f"{name}.json"
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        size_txt = f"{size // 1024} KB" if size >= 1024 else f"{size} B"

        parts: list[str] = [
            f'<h3 style="margin-bottom:4px">{name}</h3>',
            f'<table style="margin:0 0 6px 0"><tr>'
            f'<td><b>{_t("Size")}:</b></td><td>{size_txt}</td></tr>',
        ]
        if mi:
            rows = []
            if mi.get("oid"):
                rows.append(("OID", f'.{mi["oid"]}'))
            if mi.get("organization"):
                rows.append((_t("Organization"), mi["organization"]))
            if mi.get("contactinfo"):
                rows.append((_t("Contact"),
                             mi["contactinfo"].replace("\n", "<br>")))
            if mi.get("lastupdated"):
                rows.append((_t("Last updated"),
                             self._fmt_timestamp(mi["lastupdated"])))
            for k, v in rows:
                parts.append(
                    f'<tr><td style="padding-right:12px"><b>{k}:</b></td>'
                    f'<td>{v}</td></tr>')
        parts.append("</table>")

        if mi and mi.get("description"):
            parts.append(
                f'<p><b>{_t("Description")}:</b><br>{mi["description"]}</p>')

        if mi:
            revs = mi.get("revisions") or []
            if revs:
                parts.append(f'<p><b>{_t("Revisions")}:</b></p><ul>')
                for r in revs:
                    parts.append(f'<li>{self._fmt_timestamp(r.get("revision",""))} '
                                 f'— {r.get("description","")}</li>')
                parts.append("</ul>")

        # Imports: links to other modules. pysmi groups them
        # {mod_name: [sym, …]} plus a meta key "class":"imports" that we skip.
        imports = data.get("imports") or {}
        imp_items = [(m, s) for m, s in sorted(imports.items())
                     if m != "class" and isinstance(s, list)]
        if imp_items:
            parts.append(
                f'<p><b>{_t("Imports from")}</b> ({len(imp_items)}):</p><ul>')
            for mod, syms in imp_items:
                link = f'<a href="{mod}">{mod}</a>'
                sym_text = ", ".join(sorted(syms))
                parts.append(f'<li>{link} — <i>{sym_text}</i></li>')
            parts.append("</ul>")

        # Dependents — lazy build.
        if self._dependents is None:
            self._dependents = self._build_dependents_index()
        deps = self._dependents.get(name) or set()
        if deps:
            parts.append(f'<p><b>{_t("Imported by")}</b> ({len(deps)}):</p><ul>')
            for m in sorted(deps):
                parts.append(f'<li><a href="{m}">{m}</a></li>')
            parts.append("</ul>")

        # Counts of definition classes (how many ObjectType / TC / etc.)
        counts: dict[str, int] = {}
        for k, v in data.items():
            if k in ("imports", "meta", "_symtable_cache_", ""):
                continue
            if not isinstance(v, dict):
                continue
            cls = v.get("class") or "?"
            counts[cls] = counts.get(cls, 0) + 1
        if counts:
            items = ", ".join(f"{k} ({v})" for k, v in
                              sorted(counts.items(), key=lambda x: -x[1]))
            parts.append(f"<br><b>{_t('Definitions')}:</b> {items}<br>")

        meta = data.get("meta") or {}
        src_comment = next((c for c in meta.get("comments", [])
                            if isinstance(c, str) and "source" in c.lower()), "")
        if src_comment:
            parts.append(f"<br><small>{src_comment}</small>")

        return "".join(parts)

    def _accept(self) -> None:
        enabled: list[str] = []
        for r in range(self.tbl.rowCount()):
            cb = self.tbl.cellWidget(r, 0)
            if isinstance(cb, QCheckBox) and cb.isChecked():
                enabled.append(self.tbl.item(r, 1).text())
        # If EVERY present module is checked, record None so future adds
        # are auto-enabled.
        total = self.tbl.rowCount()
        self.settings.enabled_mibs = None if len(enabled) == total else enabled
        self.settings.save()
        self.accept()
