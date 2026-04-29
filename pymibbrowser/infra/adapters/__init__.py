"""Concrete adapters wiring engine ports to real-world libraries.

Each adapter is a thin class — translates between an engine Protocol and one
specific dependency (pysnmp, MibTree, the system clock, the filesystem). No
globals, no shared state, no logic beyond translation: anything more
interesting belongs in either the engine or the underlying infra module.
"""
from .clock import WallClock
from .file_sink import FileSink, NullSink
from .logger import CallbackLogger, PrintLogger
from .mib_compiler import PysmiMibCompiler
from .mib_store import MibTreeStore
from .notifier import CallbackNotifier, DesktopNotifier
from .resolver import MibTreeResolver, NumericResolver
from .settings import JsonFileSettingsStore, default_settings_store
from .snmp import PysnmpTransport
from .traps import PysnmpTrapPublisher, UdpTrapSubscription

__all__ = [
    "CallbackLogger",
    "CallbackNotifier",
    "DesktopNotifier",
    "FileSink",
    "JsonFileSettingsStore",
    "MibTreeResolver",
    "MibTreeStore",
    "NullSink",
    "NumericResolver",
    "PrintLogger",
    "PysmiMibCompiler",
    "PysnmpTransport",
    "PysnmpTrapPublisher",
    "UdpTrapSubscription",
    "WallClock",
    "default_settings_store",
]
