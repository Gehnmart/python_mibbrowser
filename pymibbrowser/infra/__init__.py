"""Concrete integrations and adapters: pysnmp, pysmi, sockets, files.

The pure execution engine lives in ``pymibbrowser.engine``. This package
holds everything that talks to the world outside it — SNMP transport,
MIB compilation/loading, trap I/O, the agent simulator, settings
persistence, i18n. Adapters in ``infra.adapters`` translate between
engine ports and these concrete modules.

Importing this package installs back-compat shims onto pure data types
in ``engine.model`` (e.g. ``AppSettings.load`` / ``settings.save``) so
existing UI callers keep working until they migrate to explicit
SettingsStore use.
"""
# Import the settings adapter at package init so AppSettings.load /
# settings.save shims are wired up before anyone touches them. Doing
# this here (rather than from infra.config) breaks an otherwise
# circular import chain: config → adapters/__init__ → snmp adapter →
# infra.snmp_ops → config.
from .adapters import settings as _settings_adapter  # noqa: F401
