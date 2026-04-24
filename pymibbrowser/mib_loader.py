"""
MIB loading, compilation, and tree building.

Two responsibilities:
  1. Compile ASN.1 MIB sources (iReasoning format) into pysnmp's JSON format
     via pysmi — so that pysnmp can translate OID names ↔ numeric OIDs.
  2. Build an in-memory tree of OID nodes for the GUI tree view.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from pysmi import debug as _pysmi_debug
from pysmi.codegen import JsonCodeGen
from pysmi.compiler import MibCompiler
from pysmi.parser import SmiV1CompatParser
from pysmi.reader import FileReader, HttpReader
from pysmi.searcher import StubSearcher, AnyFileSearcher
from pysmi.writer import FileWriter

from . import config

log = logging.getLogger(__name__)


# Stub modules that are implicitly provided by pysnmp at runtime — we shouldn't
# try to compile them. Matches pysmi's baseMibs list.
# pysmi's framework mibs — these are built-in metadata modules, not useful to
# compile (no OIDs of their own). SNMPv2-MIB is NOT included here because it
# defines widely-used nodes (sysName, sysContact, ...) that we want in the tree.
STUB_MIBS = (
    "RFC-1212", "RFC-1215", "RFC1155-SMI", "RFC1158-MIB",
    "SNMPv2-SMI", "SNMPv2-TC", "SNMPv2-CONF",
    "ASN1", "ASN1-ENUMERATION", "ASN1-REFINEMENT",
)


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------

def _make_compiler(src_dirs: list[Path], dest: Path,
                   use_network: bool = False) -> MibCompiler:
    searchers = [
        StubSearcher(*STUB_MIBS),
        AnyFileSearcher(str(dest)).set_options(exts=[".json"]),
    ]
    readers: list = [FileReader(str(d), recursive=True) for d in src_dirs]
    if use_network:
        try:
            readers.append(HttpReader("https://mibs.pysnmp.com/asn1/@mib@"))
        except TypeError:
            readers.append(HttpReader("mibs.pysnmp.com", 443, "/asn1/@mib@"))

    c = MibCompiler(SmiV1CompatParser(tempdir=""),
                    JsonCodeGen(),
                    FileWriter(str(dest)).set_options(suffix=".json"))
    c.add_sources(*readers)
    c.add_searchers(*searchers)
    return c


def _discover_modules(src_dirs: list[Path]) -> list[str]:
    mods: list[str] = []
    seen: set[str] = set()
    for d in src_dirs:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            name = p.name
            mod = (p.stem if name.lower().endswith((".mib", ".my", ".txt", ".smi"))
                   else name)
            mod = mod.upper()
            if mod in seen or mod in STUB_MIBS:
                continue
            seen.add(mod)
            mods.append(mod)
    return mods


def compile_mibs(src_dirs: list[Path], dest: Path, verbose: bool = False,
                 on_progress=None, rebuild: bool = False,
                 use_network: bool = False) -> dict:
    """
    Compile every .mib / .my / .txt / bare-name ASN.1 source under src_dirs
    into pysnmp JSON in dest.

    If ``on_progress`` is given, it's called after each module as
    ``on_progress(module_name, status, done, total)`` — perfect for driving
    a Qt progress bar. Internally we iterate modules one at a time instead
    of calling ``compile(*all_modules)``; pysmi already caches resolved
    deps in ``dest``, so the per-module overhead is negligible after the
    first few modules.

    Returns the merged pysmi status dict over all modules.
    """
    dest.mkdir(parents=True, exist_ok=True)
    compiler = _make_compiler(src_dirs, dest, use_network=use_network)
    modules = _discover_modules(src_dirs)
    total = len(modules)
    log.info("Compiling %d MIB modules into %s (network=%s)",
             total, dest, use_network)

    merged: dict = {}
    import time as _time
    for i, mod in enumerate(modules, 1):
        # Log BEFORE the call: if pysmi hangs inside a bad MIB, the file
        # log shows exactly which module was the last one attempted.
        log.info("[%d/%d] compiling %s …", i, total, mod)
        t0 = _time.monotonic()
        try:
            res = compiler.compile(mod, rebuild=rebuild, genTexts=True,
                                    ignoreErrors=True)
        except Exception as exc:
            res = {mod: f"failed: {exc}"}
            log.exception("[%d/%d] exception while compiling %s",
                          i, total, mod)
        dt = _time.monotonic() - t0
        status = res.get(mod, "")
        if dt > 3:
            log.warning("[%d/%d] %s → %s in %.1fs (slow)",
                        i, total, mod, status, dt)
        else:
            log.info("[%d/%d] %s → %s in %.2fs",
                     i, total, mod, status, dt)
        merged.update(res)
        if on_progress is not None:
            try:
                on_progress(mod, status, i, total)
            except Exception:
                log.exception("progress callback error")
    return merged


def compile_modules(module_names: list[str], extra_src_dirs: list[Path],
                    dest: Path, use_network: bool = False) -> dict:
    """
    Compile the named modules. pysmi resolves each module's IMPORTS
    recursively — it walks FileReader (extra_src_dirs + the default MIB
    directory) and falls back to HttpReader to pull missing deps from
    mibs.pysnmp.com. Returns {module_name: status} from pysmi.
    """
    dest.mkdir(parents=True, exist_ok=True)
    from . import config
    src_dirs = list(extra_src_dirs) + [config.default_mibs_src()]
    compiler = _make_compiler(src_dirs, dest, use_network=use_network)
    return compiler.compile(*module_names, rebuild=True, genTexts=True,
                            ignoreErrors=True)


# ---------------------------------------------------------------------------
# Tree model
# ---------------------------------------------------------------------------

@dataclass
class MibNode:
    name: str                              # symbolic, e.g. sysUpTime
    oid: tuple[int, ...]                   # numeric, e.g. (1,3,6,1,2,1,1,3)
    module: str = ""
    node_type: str = ""                    # ObjectType, NotificationType, etc.
    # pysmi's authoritative role classification: scalar / column / row / table /
    # notification / group / compliance / "" for containers we synthesized.
    # Used instead of heuristics to decide GET-with-.0, Table-View etc.
    node_role: str = ""
    syntax: str = ""                       # Integer32, OCTET STRING, etc.
    access: str = ""                       # read-only / read-write / ...
    status: str = ""                       # current / deprecated / obsolete
    description: str = ""
    units: str = ""
    indices: list[str] = field(default_factory=list)        # index column names
    implied_last_index: bool = False        # last index has no length prefix
    enum_values: dict[int, str] = field(default_factory=dict)
    children: list["MibNode"] = field(default_factory=list)
    parent: Optional["MibNode"] = None

    @property
    def is_table(self) -> bool:
        return self.node_role == "table"

    @property
    def is_table_entry(self) -> bool:
        return self.node_role == "row"

    @property
    def is_scalar(self) -> bool:
        return self.node_role == "scalar"

    @property
    def is_column(self) -> bool:
        return self.node_role == "column"

    def add_child(self, node: "MibNode") -> None:
        node.parent = self
        self.children.append(node)

    def sort_children_by_oid(self) -> None:
        self.children.sort(key=lambda n: n.oid)
        for c in self.children:
            c.sort_children_by_oid()

    def path(self) -> str:
        parts: list[str] = []
        n: Optional[MibNode] = self
        while n is not None:
            parts.append(n.name)
            n = n.parent
        return ".".join(reversed(parts))


class MibTree:
    """
    In-memory representation of everything pysmi compiled. Supports resolving
    a name to an OID, an OID to its nearest named ancestor, and iteration for
    GUI display.
    """

    # Well-known parts of the OID tree that no MIB re-defines but which we want
    # to render with symbolic names so that a GET on `sysUpTime` isn't shown
    # as `.3.6.1.2.1.1.3` above the nearest-named ancestor.
    _BOOTSTRAP = (
        ((1,),                         "iso"),
        ((1, 3),                       "org"),
        ((1, 3, 6),                    "dod"),
        ((1, 3, 6, 1),                 "internet"),
        ((1, 3, 6, 1, 1),              "directory"),
        ((1, 3, 6, 1, 2),              "mgmt"),
        ((1, 3, 6, 1, 2, 1),           "mib-2"),
        ((1, 3, 6, 1, 2, 1, 10),       "transmission"),
        ((1, 3, 6, 1, 3),              "experimental"),
        ((1, 3, 6, 1, 4),              "private"),
        ((1, 3, 6, 1, 4, 1),           "enterprises"),
        ((1, 3, 6, 1, 5),              "security"),
        ((1, 3, 6, 1, 6),              "snmpV2"),
        ((1, 3, 6, 1, 6, 3),           "snmpModules"),
    )

    def __init__(self) -> None:
        self.root = MibNode(name="iso", oid=(1,))
        self._by_oid: dict[tuple[int, ...], MibNode] = {(1,): self.root}
        self._by_name: dict[str, MibNode] = {"iso": self.root}
        self.modules: dict[str, dict] = {}
        # Textual conventions: {tc_name: enum_values}. Populated during load.
        # ObjectType nodes whose syntax names a TC inherit that TC's enum map.
        self.tc_enums: dict[str, dict[int, str]] = {}
        # Populated by load_compiled() when the settings-enabled list asked
        # for modules whose JSON is gone from the cache. UI reads this to
        # warn the user.
        self.missing_enabled: list[str] = []
        self._install_bootstrap()

    def _install_bootstrap(self) -> None:
        """Pre-populate canonical SMI node names so the tree renders nicely
        even before any module merges happen."""
        for oid, name in self._BOOTSTRAP:
            if oid in self._by_oid:
                continue
            parent_oid = oid[:-1]
            parent = self._by_oid.get(parent_oid)
            if parent is None:
                continue
            node = MibNode(name=name, oid=oid)
            parent.add_child(node)
            self._by_oid[oid] = node
            self._by_name[name] = node

    def load_compiled(self, compiled_dir: Path,
                      enabled: Optional[list[str]] = None) -> int:
        """Load compiled MIB JSON files. If `enabled` is given (a list of
        module names), only those modules are merged into the tree — other
        files still exist on disk but don't populate the tree. None means
        "load everything"."""
        compiled_dir = Path(compiled_dir)
        if not compiled_dir.exists():
            return 0
        wanted = set(enabled) if enabled is not None else None
        # If the user requested specific modules but some of their JSONs
        # are gone (user deleted a compiled file), surface that in the log
        # once — otherwise it's silently skipped and they wonder why their
        # tree is missing symbols.
        if wanted is not None:
            present = {p.stem for p in compiled_dir.glob("*.json")}
            missing = sorted(wanted - present)
            if missing:
                log.warning(
                    "enabled-but-missing MIB modules (%d): %s — "
                    "they were deleted from %s and will be skipped. "
                    "Reload via File → Load MIB or remove from File → MIB Modules.",
                    len(missing), ", ".join(missing), compiled_dir)
                self.missing_enabled: list[str] = missing   # for UI readback
        count = 0
        # First pass: harvest TCs from ALL compiled files so that enabled
        # modules whose enum syntax references a TC in a disabled module
        # still inherit enums.
        for jp in sorted(compiled_dir.glob("*.json")):
            try:
                data = json.loads(jp.read_text())
            except Exception:
                continue
            self._harvest_textual_conventions(data)
        # Second pass: merge only the enabled ones into the tree.
        for jp in sorted(compiled_dir.glob("*.json")):
            mod_name = jp.stem
            if wanted is not None and mod_name not in wanted:
                continue
            try:
                data = json.loads(jp.read_text())
            except Exception as exc:
                log.warning("skip %s: %s", jp.name, exc)
                continue
            self.modules[mod_name] = data
            count += 1
            self._merge_module(mod_name, data)
        self.root.sort_children_by_oid()
        return count

    def _harvest_textual_conventions(self, data: dict) -> None:
        """
        Scan a compiled module for class=='textualconvention' entries and
        record their enum mapping (if any). We also follow simple TC → TC
        aliases: if TC B just wraps TC A, B inherits A's enums.
        """
        aliases: list[tuple[str, str]] = []   # (new_tc, parent_tc)
        for name, body in data.items():
            if not isinstance(body, dict):
                continue
            if body.get("class") != "textualconvention":
                continue
            tname = body.get("name") or name
            tbody = body.get("type") or {}
            enums = (tbody.get("constraints") or {}).get("enumeration")
            if isinstance(enums, dict):
                # pysmi uses {"name": value, ...}
                emap: dict[int, str] = {}
                for k, v in enums.items():
                    try:
                        emap[int(v)] = str(k)
                    except (TypeError, ValueError):
                        pass
                if emap:
                    self.tc_enums[tname] = emap
            else:
                parent = tbody.get("type")
                if isinstance(parent, str) and parent:
                    aliases.append((tname, parent))
        # Resolve chain: keep propagating until stable.
        changed = True
        while changed:
            changed = False
            for new_tc, parent in aliases:
                if new_tc in self.tc_enums:
                    continue
                if parent in self.tc_enums:
                    self.tc_enums[new_tc] = dict(self.tc_enums[parent])
                    changed = True

    def _merge_module(self, mod_name: str, data: dict) -> None:
        pending = dict(data)
        for k in ("imports", "meta", "_symtable_cache_", ""):
            pending.pop(k, None)

        # Sort defs by resolvable OID depth so parents arrive before children.
        # This prevents us from synthesizing a numeric-name stub for an OID
        # that will soon be named properly (e.g. ifIndex under ifTable.ifEntry).
        def _key(item):
            body = item[1]
            if not isinstance(body, dict):
                return (999, 0)
            o = body.get("oid") or ""
            parts = o.split(".") if o else []
            try:
                return (0, len(parts), tuple(int(p) for p in parts))
            except ValueError:
                return (1, len(parts))
        ordered = sorted(pending.items(), key=_key)

        # Two-phase: first pass makes nodes in OID order; second pass retries
        # forward-refs (rare, but SMI allows them).
        leftover: list[tuple[str, dict]] = []
        for name, body in ordered:
            if not isinstance(body, dict):
                continue
            node = self._try_make_node(mod_name, name, body)
            if node is None:
                leftover.append((name, body))
        progress = True
        while leftover and progress:
            progress = False
            new_leftover: list[tuple[str, dict]] = []
            for name, body in leftover:
                node = self._try_make_node(mod_name, name, body)
                if node is None:
                    new_leftover.append((name, body))
                else:
                    progress = True
            leftover = new_leftover
        if leftover:
            log.debug("%s: %d defs unresolved", mod_name, len(leftover))

    def _try_make_node(self, mod: str, name: str, body: dict) -> Optional[MibNode]:
        # Require an OID we can resolve. pysmi's JSON stores it as either
        # {"oid": "1.3.6.1.x"} (already resolved) or unresolved symbolic.
        oid_str = body.get("oid")
        if not oid_str:
            return None
        try:
            oid = tuple(int(p) for p in oid_str.split("."))
        except ValueError:
            # Symbolic? Try to resolve via parent-name lookup.
            parts = oid_str.split(".")
            parent = self._by_name.get(parts[0])
            if parent is None:
                return None
            try:
                tail = tuple(int(p) for p in parts[1:])
            except ValueError:
                return None
            oid = parent.oid + tail

        if oid in self._by_oid:
            # Already present (typically as an auto-synthesized numeric stub).
            # Enrich with proper name/type/description.
            n = self._by_oid[oid]
            self._fill_metadata(n, mod, name, body)
            if name not in self._by_name:
                self._by_name[name] = n
            return n

        node = MibNode(name=name, oid=oid, module=mod)
        self._fill_metadata(node, mod, name, body)
        # Attach under the closest known ancestor.
        parent_oid = self._longest_prefix(oid)
        parent = self._by_oid.get(parent_oid) or self.root
        # Synthesize intermediate numeric nodes if needed.
        cur = parent
        for i in range(len(parent.oid), len(oid) - 1):
            sub_oid = oid[: i + 1]
            if sub_oid not in self._by_oid:
                stub = MibNode(name=str(oid[i]), oid=sub_oid)
                cur.add_child(stub)
                self._by_oid[sub_oid] = stub
                cur = stub
            else:
                cur = self._by_oid[sub_oid]
        cur.add_child(node)
        self._by_oid[oid] = node
        self._by_name[name] = node
        return node

    def _fill_metadata(self, n: MibNode, mod: str, name: str, body: dict) -> None:
        n.name = name
        if mod:
            n.module = mod
        n.node_type = body.get("class", "") or n.node_type
        n.description = body.get("description", "") or n.description
        n.units = body.get("units", "") or n.units
        n.access = body.get("maxaccess", "") or n.access
        n.status = body.get("status", "") or n.status
        syntax = body.get("syntax")
        if isinstance(syntax, dict):
            t = syntax.get("type", "")
            cls = syntax.get("class", "")
            n.syntax = t or cls or n.syntax
            constraints = syntax.get("constraints", {}) or {}
            enums = constraints.get("enumeration") or {}
            if isinstance(enums, dict):
                for k, v in enums.items():
                    try:
                        n.enum_values[int(v)] = str(k)
                    except (TypeError, ValueError):
                        pass
        elif isinstance(syntax, str):
            n.syntax = syntax
        # Inherit enum from the textual convention named by this syntax.
        if not n.enum_values and n.syntax and n.syntax in self.tc_enums:
            n.enum_values = dict(self.tc_enums[n.syntax])
        # pysmi's authoritative role: scalar / column / row / table /
        # notification / group / compliance.
        nt = body.get("nodetype", "")
        if nt:
            n.node_role = nt
        # indices (with IMPLIED flag on the last variable-length index)
        idx = body.get("indices") or []
        if isinstance(idx, list):
            n.indices = [i.get("object", "") if isinstance(i, dict) else str(i)
                         for i in idx]
            if idx and isinstance(idx[-1], dict):
                n.implied_last_index = bool(idx[-1].get("implied"))
            if n.indices and not n.node_role:
                n.node_role = "row"

    def _longest_prefix(self, oid: tuple[int, ...]) -> tuple[int, ...]:
        for i in range(len(oid) - 1, 0, -1):
            if oid[:i] in self._by_oid:
                return oid[:i]
        return (1,)

    # Lookup API --------------------------------------------------------

    def resolve_name(self, name_or_oid: str) -> Optional[tuple[int, ...]]:
        """Resolve 'sysUpTime' or 'sysUpTime.0' or '1.3.6.1.2.1.1.3.0'."""
        s = name_or_oid.strip().lstrip(".")
        if not s:
            return None
        # Pure numeric?
        if all(part.isdigit() for part in s.split(".")):
            return tuple(int(p) for p in s.split("."))
        # name[.suffix]
        parts = s.split(".")
        head = parts[0]
        suffix = tuple(int(p) for p in parts[1:] if p.isdigit())
        node = self._by_name.get(head)
        if node is None:
            return None
        return node.oid + suffix

    def lookup_oid(self, oid: Iterable[int]) -> Optional[MibNode]:
        """Nearest named ancestor for a numeric OID."""
        t = tuple(int(x) for x in oid)
        for i in range(len(t), 0, -1):
            n = self._by_oid.get(t[:i])
            if n is not None:
                return n
        return None

    def format_oid(self, oid: Iterable[int]) -> str:
        """Human-friendly formatting: symbolName.suffix if we can."""
        t = tuple(int(x) for x in oid)
        node = self.lookup_oid(t)
        if node is None:
            return "." + ".".join(str(p) for p in t)
        if node.oid == t:
            return node.name
        suffix = t[len(node.oid):]
        return node.name + "." + ".".join(str(p) for p in suffix)

    def node_by_oid(self, oid: tuple[int, ...]) -> Optional[MibNode]:
        return self._by_oid.get(oid)

    def node_by_name(self, name: str) -> Optional[MibNode]:
        return self._by_name.get(name)


# ---------------------------------------------------------------------------
# Convenience bootstrapping
# ---------------------------------------------------------------------------

def build_tree_with_default_mibs(rebuild: bool = False,
                                  on_progress=None,
                                  use_network: bool = False) -> MibTree:
    """Compile and load all MIBs from default sources, return a MibTree.

    ``on_progress(module, status, done, total)`` is forwarded to compile_mibs."""
    dest = config.compiled_mibs_dir()
    src = config.default_mibs_src()

    if rebuild or not any(dest.glob("*.json")):
        log.info("Compiling MIBs (this takes a while on first run)...")
        compile_mibs([src], dest, on_progress=on_progress,
                     rebuild=rebuild, use_network=use_network)

    tree = MibTree()
    tree.load_compiled(dest)
    return tree
