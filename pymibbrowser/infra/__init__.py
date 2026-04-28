"""Concrete integrations and adapters: pysnmp, pysmi, sockets, files.

The pure execution engine lives in ``pymibbrowser.engine``. This package
holds everything that talks to the world outside it — SNMP transport,
MIB compilation/loading, trap I/O, the agent simulator, settings
persistence, i18n. Adapters in ``infra.adapters`` translate between
engine ports and these concrete modules.
"""
