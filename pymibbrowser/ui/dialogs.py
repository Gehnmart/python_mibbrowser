"""Small modal dialogs: Agent properties, SET value, input prompts."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget, QDoubleSpinBox, QPlainTextEdit,
)

from ..i18n import _t
from ..config import Agent
from ..mib_loader import MibNode


class AgentDialog(QDialog):
    """Edit the current SNMP agent properties. SNMPv3 fields are kept visible
    but simplified — the user said v3 is out-of-scope so they are stubs that
    pass through to pysnmp unchanged if set."""

    def __init__(self, agent: Agent, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("Agent properties"))
        self.agent = Agent(**vars(agent))        # copy

        tabs = QTabWidget(self)

        # General tab
        g = QWidget()
        gl = QFormLayout(g)
        self.host_edit = QLineEdit(self.agent.host)
        self.port_edit = QSpinBox(); self.port_edit.setRange(1, 65535); self.port_edit.setValue(self.agent.port)
        self.ver_combo = QComboBox(); self.ver_combo.addItems(("1", "2c", "3")); self.ver_combo.setCurrentText(self.agent.version)
        self.timeout_edit = QDoubleSpinBox(); self.timeout_edit.setRange(0.1, 60); self.timeout_edit.setValue(self.agent.timeout_s)
        self.retries_edit = QSpinBox(); self.retries_edit.setRange(0, 10); self.retries_edit.setValue(self.agent.retries)
        self.rcomm_edit = QLineEdit(self.agent.read_community)
        self.wcomm_edit = QLineEdit(self.agent.write_community)
        self.maxrep_edit = QSpinBox(); self.maxrep_edit.setRange(1, 200); self.maxrep_edit.setValue(self.agent.max_repetitions)
        self.nonrep_edit = QSpinBox(); self.nonrep_edit.setRange(0, 50); self.nonrep_edit.setValue(self.agent.non_repeaters)

        gl.addRow(_t("Host"), self.host_edit)
        gl.addRow(_t("Port"), self.port_edit)
        gl.addRow(_t("Version"), self.ver_combo)
        gl.addRow(_t("Timeout (s)"), self.timeout_edit)
        gl.addRow(_t("Retries"), self.retries_edit)
        gl.addRow(_t("Read community"), self.rcomm_edit)
        gl.addRow(_t("Write community"), self.wcomm_edit)
        gl.addRow(_t("Max repetitions (bulk)"), self.maxrep_edit)
        gl.addRow(_t("Non repeaters (bulk)"), self.nonrep_edit)
        tabs.addTab(g, _t("General"))

        # v3 tab — passthrough only
        v3 = QWidget()
        vl = QFormLayout(v3)
        self.user_edit = QLineEdit(self.agent.user)
        self.auth_proto = QComboBox(); self.auth_proto.addItems(("none","md5","sha"))
        self.auth_proto.setCurrentText(self.agent.auth_protocol)
        self.auth_pass = QLineEdit(self.agent.auth_password); self.auth_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.priv_proto = QComboBox(); self.priv_proto.addItems(("none","des","3des","aes","aes128","aes192","aes256"))
        self.priv_proto.setCurrentText(self.agent.priv_protocol)
        self.priv_pass = QLineEdit(self.agent.priv_password); self.priv_pass.setEchoMode(QLineEdit.EchoMode.Password)
        vl.addRow(_t("User"), self.user_edit)
        vl.addRow(_t("Auth proto"), self.auth_proto)
        vl.addRow(_t("Auth password"), self.auth_pass)
        vl.addRow(_t("Priv proto"), self.priv_proto)
        vl.addRow(_t("Priv password"), self.priv_pass)
        tabs.addTab(v3, _t("SNMPv3 (passthrough)"))

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self) -> None:
        self.agent.host = self.host_edit.text().strip()
        self.agent.port = int(self.port_edit.value())
        self.agent.version = self.ver_combo.currentText()
        self.agent.timeout_s = float(self.timeout_edit.value())
        self.agent.retries = int(self.retries_edit.value())
        self.agent.read_community = self.rcomm_edit.text()
        self.agent.write_community = self.wcomm_edit.text()
        self.agent.max_repetitions = int(self.maxrep_edit.value())
        self.agent.non_repeaters = int(self.nonrep_edit.value())
        self.agent.user = self.user_edit.text()
        self.agent.auth_protocol = self.auth_proto.currentText()
        self.agent.auth_password = self.auth_pass.text()
        self.agent.priv_protocol = self.priv_proto.currentText()
        self.agent.priv_password = self.priv_pass.text()
        self.accept()


class SetDialog(QDialog):
    """Prompt for (type, value) for an SNMP SET."""

    TYPES = [("i", "Integer32"), ("u", "Unsigned32"), ("t", "TimeTicks"),
             ("a", "IpAddress"), ("o", "OID"), ("s", "String"),
             ("c", "Counter32"), ("g", "Gauge32"), ("x", "Hex (0x..)")]

    def __init__(self, oid_text: str, node: Optional[MibNode], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Set {oid_text}")

        form = QFormLayout(self)
        self.type_combo = QComboBox()
        for code, label in self.TYPES:
            self.type_combo.addItem(f"{code} — {label}", code)
        # Guess a reasonable default from MIB syntax
        default_tag = "s"
        if node and node.syntax:
            s = node.syntax.lower()
            if "integer" in s: default_tag = "i"
            elif "unsigned" in s or "gauge" in s: default_tag = "g"
            elif "counter" in s: default_tag = "c"
            elif "timeticks" in s: default_tag = "t"
            elif "ipaddress" in s: default_tag = "a"
            elif "objectidentifier" in s or "oid" in s: default_tag = "o"
        for i in range(self.type_combo.count()):
            if self.type_combo.itemData(i) == default_tag:
                self.type_combo.setCurrentIndex(i)
                break

        self.value_edit = QLineEdit()
        form.addRow(QLabel(oid_text))
        form.addRow(_t("Type"), self.type_combo)
        form.addRow(_t("Value"), self.value_edit)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def result(self) -> tuple[str, str]:
        return self.type_combo.currentData(), self.value_edit.text()
