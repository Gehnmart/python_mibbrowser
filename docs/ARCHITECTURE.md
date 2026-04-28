# Architecture

## Shape

Hexagonal (ports-and-adapters). Three layers, one strict direction:

```
       ┌─────────────────── ui/  (Qt) ───┐
       │                                 │
       │     cli.py    ◀── frontends ──▶ │
       │                                 │
       └────────┬────────────────────────┘
                │ depends on
                ▼
       ┌─── infra/  (pysnmp, pysmi, sockets, files) ───┐
       │                                               │
       │   adapters/* implement engine.ports.*         │
       │                                               │
       └────────┬──────────────────────────────────────┘
                │ implements (Protocol)
                ▼
       ┌─── engine/  (stdlib + typing only) ───┐
       │                                       │
       │   model · ast · ports · parser · runner│
       │                                       │
       └───────────────────────────────────────┘
```

Engine knows nothing. Infra translates. UI orchestrates.

## Layers

### `pymibbrowser.engine` — pure brain

Stdlib + `typing` only. Imports nothing from pysnmp, pysmi, Qt, the
filesystem, or the network. Hosts:

- **`model`** — frozen dataclasses for values that cross layer
  boundaries: `Agent`, `VarBind`, `TrapEvent`, `MibNodeView`,
  `AppSettings`, `CompileResult`. No methods that talk to the world.
- **`ast`** — script command nodes (`Get`, `GetNext`, `Set`, `Sleep`,
  `Save`, `If`, `Unknown`) and the `Script` container. Frozen tuples.
- **`ports`** — `typing.Protocol` declarations for every dependency
  the runner can have: `SnmpTransport`, `Clock`, `Resolver`, `Logger`,
  `OutputSink`, `TrapPublisher`, `TrapSubscription`, `MibStore`,
  `SettingsStore`, `MibCompiler`. Structural typing — adapters don't
  inherit, they just match the shape.
- **`parser`** — text → `Script`. Single-pass, no I/O.
- **`runner`** — `execute(script, ctx)` walks the AST. Every side
  effect goes through `ctx.<port>`. No globals, no module-level state,
  no library imports.

Strict invariants enforced by mypy `disallow_untyped_*` overrides
(see `pyproject.toml [[tool.mypy.overrides]]`) and verified by
`tests/test_cli.py::test_cli_does_not_import_qt`.

### `pymibbrowser.infra` — the world

Concrete I/O and third-party integrations.

- **`adapters/`** — thin translators, one per port. Each takes the
  vendor type (rfc1902 VarBind, pysmi compiler, JSON file, UDP
  socket) and rewraps it as the engine type. **Adapter rule: dumb
  API translation, no business logic.** A typical adapter is 30–80
  LOC.
- **`config.py`** — XDG path helpers, `load_settings()` /
  `save_settings()` convenience functions. No global state.
- **`mib_loader.py`** — pure-Python MIB tree code (`MibTree`,
  `MibNode`, `STUB_MIBS`). The pysmi compile path lives in
  `adapters/mib_compiler.py`; this module just consumes the JSON.
- **`snmp_ops.py`** — pysnmp 7 async wrappers exposed as sync
  helpers (each call runs in a fresh `asyncio` loop). Used by
  `adapters/snmp.py`.
- **`trap_receiver.py`** / **`trap_sender.py`** — UDP trap listener
  and v1/v2c notification sender.
- **`simulator.py`** — embedded SNMP v1/v2c agent for testing.
- **`script_runner.py`** — Qt-bound legacy entrypoint that drives the
  engine (kept for back-compat with the GUI button).

### `pymibbrowser.ui` — Qt frontend

`MibBrowserWindow` and dialogs. UI consumes the engine through
adapters; it never imports `pysnmp`/`pysmi`/`json` directly for
business logic. UI-specific concerns (Qt thread safety, signal
plumbing) live here and in `pymibbrowser.qt_safety`.

### `pymibbrowser.cli` — non-Qt frontend

Proof that the engine is reusable. Subcommands `run`, `modules`,
`walk`, `compile-mibs`, `sniff-traps` build the same adapter set the
GUI uses, with CLI-shaped logger/sink. Imports zero Qt — guarded by
a subprocess assertion in `tests/test_cli.py`.

## Invariants

These are the rules the structure exists to enforce. Violating any
of them silently degrades the architecture; CI / tests catch the
ones we know how to detect.

1. **Engine has no third-party imports.** Verified by mypy strict
   overrides and by reading the imports — there are no exceptions.
2. **Adapters are thin translators.** No business logic, no caching,
   no policy. If you find yourself writing `if X: return Y` in an
   adapter that's not just type conversion, the rule is being broken
   — the decision belongs in the engine or the frontend.
3. **No globals.** State lives in dataclass instances or
   `ExecutionContext`. The few module-level names that exist are
   constants (`STUB_MIBS`, `_NESTED_LOADERS`).
4. **Frozen data crosses layers.** Engine's `VarBind`, `TrapEvent`,
   `MibNodeView`, `Script` are all frozen dataclasses / tuples — an
   adapter can't accidentally mutate them upstream.
5. **Adapters monkeypatch through module attributes.** Tests stub
   `infra.snmp_ops.op_get` etc. via attribute reassignment;
   adapters call `snmp_ops.op_get(...)` not `from .snmp_ops import
   op_get`, so the patch takes effect.
6. **CLI must not import Qt.** Subprocess test in
   `tests/test_cli.py::test_cli_does_not_import_qt`.

## Adding things

### A new frontend

Build the adapter set you need, hand it to `engine.runner.execute`
(or call individual ports directly). See `cli.py` for the minimal
recipe — five subcommands, ~280 LOC, no Qt:

```python
from pymibbrowser.engine.runner import ExecutionContext, execute
from pymibbrowser.engine.parser import parse_script
from pymibbrowser.infra.adapters import (
    PysnmpTransport, WallClock, MibTreeResolver,
    MibTreeStore, PrintLogger, FileSink,
)

store = MibTreeStore(compiled_dir, enabled=enabled_mibs or [])
ctx = ExecutionContext(
    agent=agent,
    snmp=PysnmpTransport(),
    clock=WallClock(),
    resolver=MibTreeResolver(store.tree),
    logger=PrintLogger(),
    sink=FileSink(),
)
execute(parse_script(text), ctx)
```

### A new port

Add a `Protocol` to `engine/ports.py`. Don't import any types from
adapters — only engine model types. Then add a port field to
`ExecutionContext` (or use it directly without going through the
runner).

### A new adapter

One file per concern under `infra/adapters/`. Re-export from
`infra/adapters/__init__.py`. Keep it under 100 LOC; if it grows
larger, the logic probably belongs in the engine or in a service in
`infra/` that the adapter wraps.

## Data flow

### Script execution

```
parse_script(text)            → Script (tuple of frozen Command nodes)
  → execute(script, ctx)
    → for cmd in script.commands:
        if isinstance(cmd, Get):       _run_get_or_next(cmd, ctx, state)
        elif isinstance(cmd, Set):     _run_set(cmd, ctx, state)
        elif isinstance(cmd, Sleep):   _interruptible_sleep(...)
        elif isinstance(cmd, Save):    ctx.sink.open(target)
        elif isinstance(cmd, If):      _run_if(cmd, ctx, state)
        elif isinstance(cmd, Unknown): _log_unknown(cmd, ctx)
    ctx.sink.close()
```

`_RunState` carries `last_result` / `last_error` between commands.
The runner never holds state across `execute()` calls.

### MIB loading

1. **Compile**: `PysmiMibCompiler.compile(modules, src_dirs, ...)`
   drives pysmi one module at a time so per-module progress fires
   between modules. JSON output drops into the cache dir.
2. **Load tree**: `MibTreeStore(compiled_dir, enabled=...)`
   constructs a `MibTree` and calls `load_compiled`:
   - First pass — harvest textual-convention enum maps from every
     module so a TC defined in disabled module A is still inherited
     by enabled module B.
   - Second pass — merge nodes from enabled modules into a tree
     rooted at `iso (1)`.
3. **Resolve**: `MibTreeResolver(store.tree)` (or
   `_DynamicResolver` from `MibTreeStore.resolver()`) translates
   names like `sysUpTime.0` to numeric tuples.

`set_enabled()` rebuilds the tree from scratch. `_DynamicResolver`
forwards through the store, so a resolver handed to the engine
survives module-set changes.

### MibNode role classification

pysmi's `nodetype` field drives the classification:

| nodetype | `is_scalar` | `is_column` | `is_table_entry` | `is_table` |
|----------|:-----------:|:-----------:|:----------------:|:----------:|
| scalar   | ✓ | | | |
| column   |   | ✓ | | |
| row      |   |   | ✓ | |
| table    |   |   |   | ✓ |

`_instance_oid` uses this: scalar → append `.0`, everything else →
bare OID.

### Index-suffix parsing (Table View)

`TableViewTab._parse_index_suffix` walks `entry.indices` and consumes
suffix bytes per column:

- `IpAddress` → 4 bytes, dotted quad
- `InetAddressType` → 1 int (enum), remembered for the next
  `InetAddress` to disambiguate v4 vs v6
- `InetAddress` / `OCTET STRING` / `PhysAddress` → length-prefixed
  octets
- `OID` / `ObjectName` → length-prefixed
- IMPLIED flag (RFC 2578 §7.7) — last variable-length index has no
  length prefix
- Anything else with `enum_values` or name ending `Type` → 1 int
- Integer family → 1 int

### SNMP ops pipeline (GUI path)

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

**Lesson** (`pymibbrowser.qt_safety`): the worker QObject needs a
Python reference (stashed on `thread._worker_ref`) or Python GCs it
before QThread wakes up and `thread.started.connect(worker.run)`
becomes a stale connection.

### Parallel column walk

`snmp_ops.async_table_walk(agent, col_oids, on_progress)` sends all
N column OIDs in one GETBULK PDU per round-trip. Each response has
N × max_repetitions varbinds in repetition-major order; advance per
column; a column exits when its next OID leaves its sub-tree or hits
EndOfMibView. 3× faster than N sequential walks AND eliminates
row-inconsistency on live tables.

### Traps

- `trap_receiver.TrapListener` runs a daemon thread on a UDP socket,
  decoding via pysnmp proto API. v2c first (superset), fall back to
  v1 on decode error (v1 Message spec lacks tag 7).
- `UdpTrapSubscription` (`adapters/traps.py`) wraps the listener as
  the engine's `TrapSubscription` port — strips the listener's
  UI-enrichment fields (severity, message) on the way through.
- Rule matching (UI only): OID wildcard (`*` → `[\d.]*`, `?` →
  `[\d]?`), IP allowlist, payload substring. Rules apply in
  alphabetical order; a later matching rule overwrites an earlier
  one's severity/message.

## Settings persistence

`engine.model.AppSettings` is a pure dataclass — no I/O methods.
Persistence belongs in `infra.adapters.settings.JsonFileSettingsStore`
(via the `SettingsStore` port). Atomic writes (tmp + rename within
the same directory).

Default location: `~/.config/pymibbrowser/settings.json`. Trap rules
live in a separate file: `~/.config/pymibbrowser/trap_rules.json`.

The store accepts only top-level JSON objects; corrupt files,
arrays, or scalar JSON values fall back to defaults so a bad save
can't brick startup.

## Quality gates

- **ruff** (lint, import sorting) — `.github/workflows/ci.yml`
- **mypy** (permissive globally; strict on `engine.*` and
  `infra.adapters.*`) — `pyproject.toml [[tool.mypy.overrides]]`
- **bandit** (security smells)
- **pytest** with `QT_QPA_PLATFORM=offscreen` for headless Qt
- **mutmut** (mutation testing on `engine/`) —
  `.github/workflows/mutation.yml`, weekly + on engine-touching
  pushes. Survivor budget enforced.

## Known rough edges

- SNMPv3 has UI fields but is untested end-to-end.
- pysmi occasionally fails to parse vendor MIBs with weird ASN.1
  (nested `OBJECT-IDENTITY` with mis-ordered clauses). Those modules
  show `failed` status; nothing crashes.
- Graph's rate mode assumes the counter never wraps; for
  `Counter32` at line rate that's a lie, but for monitoring
  purposes it's fine.
