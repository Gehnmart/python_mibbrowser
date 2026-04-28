"""Concrete adapters wiring engine ports to real-world libraries.

Each adapter is a thin class — translates between an engine Protocol and one
specific dependency (pysnmp, MibTree, the system clock, the filesystem). No
globals, no shared state, no logic beyond translation: anything more
interesting belongs in either the engine or the underlying infra module.
"""
from .clock import WallClock
from .file_sink import FileSink, NullSink
from .logger import CallbackLogger, PrintLogger
from .mib_store import MibTreeStore
from .resolver import MibTreeResolver, NumericResolver
from .snmp import PysnmpTransport

__all__ = [
    "WallClock",
    "FileSink",
    "NullSink",
    "CallbackLogger",
    "PrintLogger",
    "MibTreeResolver",
    "MibTreeStore",
    "NumericResolver",
    "PysnmpTransport",
]
