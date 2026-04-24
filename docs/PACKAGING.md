# Packaging

## Portable build via PyInstaller

`./build.sh` produces `dist/pymibbrowser-linux-x86_64/`:

```
dist/pymibbrowser-linux-x86_64/
├── pymibbrowser          ← executable (the bundled Python runtime + deps)
├── _internal/            ← PyInstaller support libs
├── mibs-src/             ← bundled MIBs (copied at build time)
├── samples/              ← demo walk dump for the simulator
├── README.md
├── LICENSE
└── run.sh                ← convenience launcher
```

Ship the **whole folder** as a tarball or zip:

```bash
tar czf pymibbrowser-linux-x86_64.tar.gz -C dist pymibbrowser-linux-x86_64/
```

On the target machine: untar, `cd`, `./pymibbrowser` (or `bash run.sh`).

### What ends up in the bundle

- Python 3.12 runtime (from the user's system Python at build time)
- PyQt6 (including Qt shared libs)
- pysnmp 7, pysmi 1.6, pyqtgraph
- pyasn1, guava-equivalents, etc.
- Our package code and bundled MIBs

Typical size: **~150 MB** uncompressed, **~45 MB** as `.tar.gz`.

### Requirements

- Linux x86_64 (the binary is not cross-platform; rebuild on macOS /
  Windows separately)
- GLIBC ≥ 2.31 (Ubuntu 20.04+). For older distros build on an older
  glibc system.
- Target needs X11 / Wayland and the standard Qt plugins (xcb is
  included by PyInstaller's Qt6 hook).

### Rebuilding after code changes

`./build.sh` is idempotent — delete `build/` and `dist/` between runs if
you want a clean rebuild.

## Alternative: source-only install

Users that already have Python 3.12 can do:

```bash
git clone <url> pymibbrowser
cd pymibbrowser
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh
```

Smaller (~25 MB for the venv + MIBs), but the user must have Python + a
system `libsnmp` is not needed (we don't use net-snmp, we use pysnmp).

## Building on macOS / Windows

Not tested. PyInstaller is cross-platform so it should work on macOS
natively. Windows build would produce `pymibbrowser.exe`; the Trap
Receiver's `PermissionError` for port 162 applies there too.
