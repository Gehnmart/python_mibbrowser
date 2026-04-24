# Usage

## Main window layout

```
┌──────────────┬──────────────────────────────────────────────────┐
│ Filter       │  Address [__________]  Advanced  OID [____]  Op  │
│              │                                                   │
│  MIB tree    │  ┌ Result ┬ Table: ifTable ┬ Graph: ifInOctets ┐  │
│              │  │ Name   │ Value │ Type │ Source │               │
│              │  │ …      │ …     │ …    │ …      │               │
│              │  └─────────────────────────────────┘               │
│  (org, dod,  │                                                    │
│   internet,  │  Log output (colour-coded by >>> <<< !!! prefix)   │
│   …)         │                                                    │
│              │                                                    │
├──────────────┤                                                    │
│ Name=…       │                                                    │
│ OID=…        │                                                    │
│ MIB=…        │                                                    │
│ …            │                                                    │
└──────────────┴──────────────────────────────────────────────────┘
 Ready.                                              [progress bar]
```

The horizontal splitter between the tree and the tab area, the vertical
splitter between the Result table and the log, and the splitter between
tree and properties — all are draggable.

## MIB tree

Type in the filter box to narrow the tree by substring. Recursive filter
keeps matching nodes visible along with their ancestors; matching branches
auto-expand. Debounced 250 ms so typing isn't laggy.

Single-click → OID field updates to the scalar form (`.1.3.6.1.2.1.1.3.0`
for `sysUpTime`, bare OID for columns/tables), properties pane fills,
Operation combo auto-switches to the most useful verb for this node type.

Double-click → runs the auto-picked operation immediately (or opens
Table View for a table/row).

Right-click → context menu: Get / Get Next / Walk / Table View / Graph /
Copy OID / Copy Name.

## Operations

| Verb | When it works | When it errors |
|------|---------------|----------------|
| Get | Scalar (e.g. sysUpTime.0) | Column without instance suffix → NoSuchInstance (hint in log) |
| Get Next | Any OID — walks one step | - |
| Get Bulk | SNMPv2c+ — walks max_repetitions varbinds per round-trip | SNMPv1 agents time out |
| Get Subtree / Walk | Whole subtree | - |
| Set | Accessible writable scalar/column | Uses **write community** |

Ctrl+Enter in the OID field = Go.

## Table View

`Operations → Table View…` or double-click on a table/row in the tree.
Default layout: rows = instances, columns = MIB columns. Press **Rotate**
to flip to iReasoning's column-oriented layout.

- `Refresh` — re-walk
- `Rotate` — swap row/column orientation
- `Export CSV` — dump the grid
- `Poll (s)` — set > 0 to auto-refresh every N seconds
- `Index Value` (blue column) — the raw OID instance suffix, prefixed
  with `[N]` row number. Handy for copy-paste when building a manual Get
  on a specific row.

Columns declared as table **indices** (ifIndex, ipAdEntAddr, …) are often
`not-accessible` and don't come back from the agent; pymibbrowser fills
them in from the OID suffix. Works for INTEGER, IpAddress, InetAddress
(with InetAddressType disambiguation), OCTET STRING, OID, all honouring
RFC 2578 IMPLIED semantics for the last variable-length index.

## Graph

`Operations → Graph…` or right-click a numeric leaf. Polls the OID every
N seconds (toolbar Interval).

- **Rate (delta)** — turns a monotonic counter into per-second rate
- **Grid** / **Pause** / **Restart**
- **Save PNG** / **Export CSV** / **Import CSV** — `.csv` is
  `t_seconds,value` pairs

## Trap Receiver

`Tools → Trap Receiver…`. Needs root for port 162 (or run as non-root on
port 1162+). Toolbar:

- `▶ Start` binds the UDP socket, `⏸ Stop` unbinds
- Filter text box hides rows by substring
- `Rules…` — per-trap actions. Each rule has:
  - `Trap OID matches` — wildcard (`*` matches any digits/dots, `?` matches 0-1)
  - `Source IPs allowed` — comma-separated wildcards
  - `Payload contains` — keywords (`|` or `,` separated)
  - `Action` — accept / ignore
  - `Set severity` — INFO / LOW / MEDIUM / HIGH / CRITICAL
  - `Set message` — format string with `{oid}`, `{ip}`, `{community}`

Click a row to see the full variable bindings in the lower pane.

## MIB Modules management

`File → MIB Modules…` lists every compiled MIB. Checkboxes toggle inclusion
in the tree. Quick actions:

- **All** / **None** / **Invert**
- **Only vendor (no RFC/SNMPv2)** — hides every `RFC*`, `SNMP*`,
  `IF-MIB`, `IP-MIB`, … keeping framework stubs (so textual conventions
  still resolve) and whatever else you have
- **Unload selected** — deletes the compiled JSON for those modules

Click any module → info pane shows description, OID, organisation,
contact, last updated, revisions, imports (as clickable links), dependents
(who imports from it), definition class counts.

## Loading custom MIB files

`File → Load MIB…`. Pick one or more `.mib` / `.my` / `.txt` / `.smi`
files. The dialog:

1. Copies the file into `mibs-src/` so it persists.
2. Auto-detects the "MIB repo root" — if the file is inside a directory
   that has multiple sibling MIB subdirs (a common pattern for vendor
   distributions like `eltexLtd/`, `wlc/`, `cpe/`, …), each of those
   subdirs is added as a separate search root so IMPORTS from other
   subdirs resolve.
3. Compiles via pysmi with a real progress bar per module.
4. **Optional**: "Fetch missing deps from mibs.pysnmp.com" — for
   standards MIBs your vendor imports but doesn't ship. Off by default.
5. Reloads the tree.

If compile fails with "no symbol X in module Y" — the wrong copy of
module Y was picked up. Click `Add source directory…` and point to the
specific dir that has the right version of Y.

## Rebuild MIB cache

`File → Rebuild MIB cache` — nukes the compiled JSONs and recompiles
every file in `mibs-src/`. Modal dialog asks whether to use network for
missing deps (remembered in settings). Runs in a worker thread with
**determinate per-module progress** (`Rebuild: [12/73] ETHERLIKE-MIB`).

Log file at `~/.local/share/pymibbrowser/logs/pymibbrowser.log` captures
every module's name and status with timestamps — useful if a specific
module hangs.

## Scripts

`Tools → Run Script…`. Script syntax (iReasoning-compatible):

```
# Comment
save ~/out/results.txt
get  snmp.host sysUpTime sysDescr
getnext snmp.host:1611 .1.3.6.1.2.1.1.3.0
set  snmp.host sysContact s admin@example.com
if $ err sound
if $ > 0 sleep 5
```

Types for `set`: `i` (int), `u` (uint), `t` (TimeTicks), `a` (IpAddress),
`o` (OID), `s` (string), `c` (Counter), `g` (Gauge), `x` (hex).

## Agent simulator

`Tools → Agent Simulator…` — launches a minimal SNMP v1/v2c agent on a
chosen port. Load a **snmpwalk dump** via `Browse…` (or paste the path to
a file in net-snmp / iReasoning `snmpwalk` format).

Sample: `samples/demo.walk` (98 OIDs: system, ifTable, ipAddrTable, …).

## Language

`Help → Language → English / Русский`. Saved in settings, picked up on
next launch. `$LANG` is used as a fallback when the setting is empty.

## Config files

- **Settings**: `~/.config/pymibbrowser/settings.json` —
  current agent, saved agents, language, enabled MIB modules,
  `fetch_missing_from_net`, trap port, graph points limit, trap rules
- **Compiled MIB cache**: `~/.local/share/pymibbrowser/compiled_mibs/*.json`
- **Logs**: `~/.local/share/pymibbrowser/logs/pymibbrowser.log`
  (rotated 1 MB × 5)
