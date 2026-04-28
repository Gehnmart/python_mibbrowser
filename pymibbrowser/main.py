"""Application entry point."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from .infra import config, i18n, mib_loader
from .infra.adapters import MibTreeStore, PysmiMibCompiler


def _setup_logging(log_path: Path) -> None:
    """Console + rotating file handler. ``log_path`` is the resolved
    target — caller decides whether the user picked a custom directory
    or the default applies."""
    from logging.handlers import RotatingFileHandler
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console at INFO.
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    # File at DEBUG, rotated at 1 MB × 5 backups.
    try:
        fh = RotatingFileHandler(
            str(log_path),
            maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
        logging.getLogger(__name__).info("logging to %s", log_path)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "file logging unavailable: %s", exc)


def main() -> int:
    # Resolve settings first so the file handler can write to the user's
    # configured log directory (or the platform default if they haven't
    # overridden it).
    settings = config.AppSettings.load()
    _setup_logging(config.log_file(settings.log_dir or None))
    app = QApplication(sys.argv)
    app.setApplicationName("pymibbrowser")

    # Settings take precedence over $LANG; empty string falls through to
    # locale auto-detect.
    i18n.init_language(settings.language or None)

    # Compile MIBs on first run (blocking with a progress dialog).
    compiled = config.compiled_mibs_dir()
    if not any(Path(compiled).glob("*.json")):
        dlg = QProgressDialog("Compiling bundled MIBs…", None, 0, 0)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.setMinimumDuration(0)
        dlg.show()
        app.processEvents()
        try:
            compiler = PysmiMibCompiler(compiled)
            sources = [config.default_mibs_src()]
            compiler.compile(compiler.discover(sources), sources)
        except Exception as exc:
            dlg.close()
            QMessageBox.critical(None, "MIB compile failed", str(exc))
            return 1
        dlg.close()

    # First-run default: hide vendor/enterprise MIBs. They're compiled and
    # present on disk — just not merged into the tree until the user opts
    # into them from File → MIB Modules. Persist the resolved list so the
    # modules dialog can show check-state that matches reality.
    if settings.enabled_mibs is None:
        settings.enabled_mibs = mib_loader.MibTree.default_enabled_modules(compiled)
        settings.save()
    store = MibTreeStore(compiled, enabled=settings.enabled_mibs)

    # Import late so the Qt app exists first.
    from .ui.main_window import MibBrowserWindow
    w = MibBrowserWindow(store, settings)
    w.show()

    return app.exec()
