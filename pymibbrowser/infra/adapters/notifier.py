"""Notifier adapters — engine.ports.Notifier implementations.

Best-effort: probes for an OS-native notifier at construction time and
caches the dispatcher. If nothing is available, ``notify`` raises and
the runner falls back to a tagged log line — no platform-specific
imports leak into the engine."""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable


class DesktopNotifier:
    """Cross-platform desktop notifications.

    Linux: libnotify via ``notify-send``. macOS: ``osascript``. Windows
    is unsupported (no built-in CLI surface) — construction succeeds
    but ``notify`` raises so the runner's logger fallback engages."""

    def __init__(self, app_name: str = "pyMIBBrowser") -> None:
        self._app = app_name
        self._dispatch: Callable[[str], None] | None = self._detect()

    def _detect(self) -> Callable[[str], None] | None:
        if shutil.which("notify-send"):
            app = self._app
            def _libnotify(msg: str) -> None:
                subprocess.run(
                    ["notify-send", app, msg],
                    check=False, timeout=2)
            return _libnotify
        if shutil.which("osascript"):
            app = self._app
            def _osa(msg: str) -> None:
                # AppleScript chokes on embedded double quotes; collapse
                # them to single before passing through the script.
                safe = msg.replace('"', "'")
                subprocess.run(
                    ["osascript", "-e",
                     f'display notification "{safe}" with title "{app}"'],
                    check=False, timeout=2)
            return _osa
        return None

    def notify(self, message: str) -> None:
        if self._dispatch is None:
            raise RuntimeError("no desktop-notifier backend available")
        self._dispatch(message)


class CallbackNotifier:
    """Forwards every notification to a caller-supplied callable. The
    UI uses this to route notifications through QSystemTrayIcon or a
    transient label rather than a libnotify pop-up."""

    def __init__(self, sink: Callable[[str], None]) -> None:
        self._sink = sink

    def notify(self, message: str) -> None:
        self._sink(message)
