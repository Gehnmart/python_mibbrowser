# pymibbrowser

A Python/Qt SNMP MIB browser in the spirit of iReasoning MIB Browser.
Runs on Linux. Built on PyQt6 · pysnmp 7 · pysmi · pyqtgraph.

![pymibbrowser screenshot](docs/images/main.png)

## Features

- **MIB tree** with substring filter (debounced, ~30 ms on 5 000 nodes)
- **SNMP v1/v2c** operations — GET, GET-NEXT, GET-BULK, GET-SUBTREE, WALK,
  SET. Auto-picks the right verb for scalar/column/row/table when you click
  a node (scalar → Get, column → Walk, table → Get-Subtree)
- **Result table** — name/OID, value, type, source; enum translation,
  TimeTicks formatted as `1 hour 12.16 seconds (361216)`, IpAddress as
  dotted quad
- **Table View** — grid layout with Refresh / Rotate / Export CSV / Poll
  interval. Fills not-accessible index columns (ifIndex, ipAdEntAddr, …)
  from the OID suffix. Parallel column walk via GETBULK keeps rows
  consistent on churning live tables (no more timeWait gaps in
  tcpConnTable)
- **Graph** — real-time pyqtgraph plot with pause / restart / rate (delta) /
  grid / save PNG / export / import CSV
- **Trap receiver** on UDP 162 with rule engine (OID + IP + payload
  wildcards → accept/ignore/set-severity/set-message)
- **Trap sender** — v1 / v2c with custom var-binds
- **Agent simulator** — built-in SNMP v1/v2c agent, loads a snmpwalk dump
- **MIB editor** — text editor with pysmi parse check
- **Trapd console** — headless trap tail view
- **Script runner** — iReasoning-compatible scripts (get/getnext/set/if/sleep/save)
- **MIB Modules management** — enable/disable per module, preset "only
  vendor" hides standard SMI/RFC noise, per-module info panel (description,
  OID, organisation, contact, revisions, imports, dependents)
- **Load MIB with dependency resolution** — auto-detects the repo root,
  resolves IMPORTS from local subdirs or optionally from mibs.pysnmp.com
- **i18n** — English / Русский, persisted in settings
- **Progress bars** — determinate per-module progress on Rebuild MIB cache
- **File log** at `~/.local/share/pymibbrowser/logs/pymibbrowser.log`
  (rotated 1 MB × 5)

## Quick start (source)

```bash
git clone <url> pymibbrowser
cd pymibbrowser
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh
```

First launch compiles the bundled IETF MIBs (~30 seconds), then opens the GUI.

## Portable release build (single folder, no system Python needed)

```bash
./build.sh             # produces dist/pymibbrowser-linux-x86_64/
```

The output folder is ~150 MB (PyQt6 + Python runtime bundled). Run it with
`./pymibbrowser`. See [docs/PACKAGING.md](docs/PACKAGING.md).

## Testing against a live SNMP agent

Ubuntu/Debian:

```bash
sudo apt install -y snmpd
sudo tee -a /etc/snmp/snmpd.conf > /dev/null <<'EOF'

# pymibbrowser: full read-only tree from localhost via dedicated community
view   all        included   .1                    80
rocommunity pymib 127.0.0.1 -V all
EOF
sudo systemctl restart snmpd
```

Then in the app: Address `127.0.0.1:161`, Advanced → Read community `pymib`,
click a node → Go.

## Documentation

- [docs/USAGE.md](docs/USAGE.md) — feature walkthroughs with screenshots
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — code layout, data flow,
  SNMP/MIB internals
- [docs/PACKAGING.md](docs/PACKAGING.md) — how the portable build works
- [docs/MIBS.md](docs/MIBS.md) — how MIB loading / dependency resolution
  works, how to add vendor MIBs

## What we don't do (yet)

- SNMPv3 (field exists, passthrough to pysnmp — untested)
- Device snapshot / Switch port mapper / Network discovery / Ping / Trace
  (iReasoning-specific extras — would require extra tooling)
- Watches with email/trap alerts (would require SMTP config + a persistent
  service)

## Credits

- [pysnmp](https://www.pysnmp.com/) and [pysmi](https://github.com/lextudio/pysmi)
  by LexTudio — the SNMP stack and MIB compiler
- [pyqtgraph](https://www.pyqtgraph.org/) — real-time plotting
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) — GUI toolkit
- The `mibs-src/` subdirectory ships a subset of public IETF MIBs plus a
  few common vendor MIBs redistributed from the
  [iReasoning MIB bundle](https://www.ireasoning.com/mibbrowser.shtml) —
  see [docs/MIBS.md](docs/MIBS.md) for the list.

## License

MIT. See [LICENSE](LICENSE).
