# Architecture

## Module layout

```
pymibbrowser/
├── __init__.py             — package version
├── __main__.py             — entry shim for `python -m pymibbrowser`
├── main.py                 — QApplication bootstrap, logging setup
├── config.py               — paths, Agent dataclass, AppSettings (JSON)
├── i18n.py                 — key → translation dict (ru/en)
├── mib_loader.py           — pysmi compile driver + MibTree in-memory graph
├── snmp_ops.py             — pysnmp 7 async wrappers (GET/NEXT/BULK/WALK/SET
│                             + parallel column walk), VarBind dataclass,
│                             display formatters (TimeTicks, IpAddress, …)
├── workers.py              — QThread/QObject helper for long SNMP ops
├── trap_receiver.py        — UDP listener, decodes v1/v2c traps
├── trap_sender.py          — send v1/v2c notifications via pysnmp
├── simulator.py            — minimal SNMP v1/v2c agent (load from snmpwalk)
├── script_runner.py        — iReasoning .snmp script interpreter
├── ui/
│   ├── main_window.py      — QMainWindow: menu, toolbar, tree+tabs
│   ├── mib_tree_model.py   — QAbstractItemModel + FastMibFilterProxy
│   ├── result_table.py     — QAbstractTableModel for var-bindings
│   ├── table_view.py       — table-view tab with parallel walk & Index Value
│   ├── graph_window.py     — pyqtgraph-based real-time plot tab
│   ├── trap_window.py      — Trap Receiver window + rule engine
│   ├── dialogs.py          — Agent properties, SET value
│   ├── mib_load_dialog.py  — Load MIB with progress / sources / deps
│   ├── mib_modules_dialog.py — MIB Modules management + info pane
│   └── tool_dialogs.py     — Trap Sender / Agent Sim / MIB Editor / Trapd
└── resources/              — icons (none currently)
```

## Data flow

### MIB loading

1. `mib_loader.compile_mibs([mibs-src], compiled_mibs)` invokes pysmi:
   - `FileReader(mibs-src, recursive=True)` + optional `HttpReader`
   - `SmiV1CompatParser` → JSON via `JsonCodeGen` → `FileWriter`
   - Iterates modules one at a time so we can emit per-module progress;
     pysmi's cache in `dest` handles intra-module dep resolution.
2. `MibTree.load_compiled()` reads every `*.json` from the cache:
   - **First pass**: harvest all textual-convention definitions (enum
     value maps) across every module, so TCs from module A used as
     syntax in module B get inherited.
   - **Second pass** (filtered by `settings.enabled_mibs` if set): merge
     each module's OID definitions into a tree rooted at `iso (1)`.
     Synthetic numeric stubs fill gaps where intermediate OIDs are
     defined only in higher-level modules.
3. Bootstrap stubs: `iso / org / dod / internet / mgmt / mib-2 /
   transmission / directory / experimental / private / enterprises /
   security / snmpV2 / snmpModules` — these names aren't in any
   compilable module, so we pre-populate them.

### MibNode role classification

pysmi's `nodetype` field drives the classification (replacing earlier
ancestor-walk heuristics):

| nodetype | `is_scalar` | `is_column` | `is_table_entry` | `is_table` |
|----------|:-----------:|:-----------:|:----------------:|:----------:|
| scalar   | ✓ | | | |
| column   |   | ✓ | | |
| row      |   |   | ✓ | |
| table    |   |   |   | ✓ |

`_instance_oid` uses this: scalar → append `.0`, everything else → bare
OID. `_default_op_for` picks Get / Walk / Get-Subtree / Get-Next
accordingly.

### Index-suffix parsing (Table View)

For a row OID like `1.3.6.1.2.1.6.20.1.2.1.4.127.0.0.1.22`, we need to
decompose the suffix after the column OID into the table's index columns.
`TableViewTab._parse_index_suffix` walks `entry.indices` and consumes
suffix bytes per column, honouring:

- `IpAddress` → 4 bytes, dotted quad
- `InetAddressType` → 1 int (enum: ipv4=1, ipv6=2, …), remembered for the
  next `InetAddress` to disambiguate v4 vs v6
- `InetAddress` / OCTET STRING / PhysAddress → length-prefixed octets
- OID / ObjectName → length-prefixed
- IMPLIED flag (RFC 2578 §7.7): the last variable-length index has no
  length prefix — use remaining suffix as-is
- Anything else with `enum_values` or name ending `Type` → 1 int (enum)
- Integer family (Integer32, Counter, Gauge, TimeTicks, …) → 1 int

### SNMP ops pipeline

```
User clicks Go
  → MibBrowserWindow._run_operation
    → workers.run_op spawns QThread + SnmpWorker
    → SnmpWorker.run invokes snmp_ops.op_get (or _walk / _bulk / …)
    → snmp_ops._run(async_coro) via asyncio.run()
    → pysnmp 7 async hlapi returns var-bindings
    → worker.finished.emit(vbs)
    → slot on main thread appends to ResultTableModel
```

**Critical lesson** (captured in `workers.py`): the worker QObject needs a
Python reference (stashed on `thread._worker_ref`) or Python GCs it
before QThread wakes up and `thread.started.connect(worker.run)` becomes
a stale connection. Symptom: "Table walk stuck at walking … forever".

### Fast MIB tree filter

`FastMibFilterProxy` subclasses `QSortFilterProxyModel`:

- Overrides `setFilterFixedString` to eager-precompute a post-order
  `{oid_tuple → bool}` cache of "any descendant matches the needle".
- `filterAcceptsRow` becomes O(1) dict lookup.
- Main window wraps the call in `setUpdatesEnabled(False) +
  collapseAll + setFilterFixedString + expandAll` — Qt's layout cost on
  5 000 already-visible rows dominates the filter itself; collapsing
  first cuts it from ~15 s to ~30 ms.

### Parallel column walk (Table View)

`snmp_ops.async_table_walk(agent, col_oids, on_progress)` sends all N
column OIDs in one GETBULK PDU per round-trip. Each response has
`N × max_repetitions` varbinds in repetition-major order. We advance per
column; a column exits when the next OID leaves its sub-tree or hits
EndOfMibView. 3× faster than N sequential walks AND eliminates
row-inconsistency for live tables (timeWait TCP sockets in
tcpConnTable).

### Traps

- `trap_receiver.TrapListener` runs a daemon thread on a UDP socket,
  decodes via pysnmp proto API (v1 Message spec lacks tag 7 for
  SNMPv2-Trap, so we try v2c first and fall back to v1 on decode error).
- `TrapReceiverWindow` connects a `pyqtSignal(object)` bridge so the
  listener thread posts events into Qt's event loop.
- Rule matching: OID wildcard (`*` expands to `[\d.]*`, `?` to `[\d]?`),
  IP allowlist, payload substring. Rules apply in alphabetical order; a
  later matching rule overwrites an earlier one's severity/message.

### Logging

`main._setup_logging` installs:
- Console `StreamHandler` at INFO
- `RotatingFileHandler` at DEBUG, 1 MB × 5 backups, in
  `~/.local/share/pymibbrowser/logs/pymibbrowser.log`

`main_window._log(text, level)` writes the Result-tab's log pane with
HTML colouring: `>>>` lines in blue (request), `<<<` green (response),
`!!!` red (error), hints orange.

## Settings persistence

`config.AppSettings` is a dataclass serialised to
`~/.config/pymibbrowser/settings.json`:

- `current_agent` / `saved_agents` (Agent dataclass)
- `loaded_mibs`, `bookmarks`, `trap_port`, `max_graph_points`
- `language` (`"" | "en" | "ru"`)
- `enabled_mibs: list[str] | None` — None = all compiled modules load
- `fetch_missing_from_net: bool` — off by default; Rebuild and Load
  dialogs respect and update this.

Trap rules live in a separate file: `~/.config/pymibbrowser/trap_rules.json`.

## Known rough edges

- SNMPv3 has UI fields but is untested end-to-end.
- pysmi occasionally fails to parse vendor MIBs with weird ASN.1
  (nested OBJECT-IDENTITY with mis-ordered clauses). Those modules show
  `failed` status; nothing crashes.
- Graph's rate mode assumes the counter never wraps; for large Counter32
  at line rate that's a lie, but for monitoring purposes it's fine.
