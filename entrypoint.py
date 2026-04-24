"""Top-level entry for `pyinstaller` — the PyInstaller-generated bootloader
imports this file as `__main__` which means relative imports inside
`pymibbrowser/__main__.py` don't work. A plain module-style entry does.
"""
from pymibbrowser.main import main

if __name__ == "__main__":
    raise SystemExit(main())
