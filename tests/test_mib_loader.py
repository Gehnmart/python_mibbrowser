"""mib_loader tests covering the in-memory tree builder and JSON merger.

The pysmi-driven compile path (compile_mibs, _make_compiler) is exercised
end-to-end via the tiny mibs-src/ fixtures already present in the repo —
one slow integration test, gated behind a fixture, plus a fast unit test
for _discover_modules. Everything else uses synthetic pysmi-style JSON
written to a tmp_path."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pymibbrowser.infra import mib_loader
from pymibbrowser.infra.mib_loader import (
    MibNode,
    MibTree,
    _discover_modules,
)


# --- MibNode --------------------------------------------------------------

class TestMibNode:
    def test_role_predicates(self):
        for role, expected in [
            ("table", {"is_table": True, "is_table_entry": False,
                       "is_scalar": False, "is_column": False}),
            ("row", {"is_table": False, "is_table_entry": True,
                       "is_scalar": False, "is_column": False}),
            ("scalar", {"is_table": False, "is_table_entry": False,
                       "is_scalar": True, "is_column": False}),
            ("column", {"is_table": False, "is_table_entry": False,
                       "is_scalar": False, "is_column": True}),
            ("",       {"is_table": False, "is_table_entry": False,
                       "is_scalar": False, "is_column": False}),
        ]:
            n = MibNode(name="x", oid=(1,), node_role=role)
            for prop, want in expected.items():
                assert getattr(n, prop) is want, (role, prop)

    def test_add_child_links_parent(self):
        p = MibNode(name="p", oid=(1,))
        c = MibNode(name="c", oid=(1, 2))
        p.add_child(c)
        assert c.parent is p
        assert p.children == [c]

    def test_sort_children_by_oid_recursive(self):
        root = MibNode(name="r", oid=(1,))
        a = MibNode(name="a", oid=(1, 5))
        b = MibNode(name="b", oid=(1, 2))
        c = MibNode(name="c", oid=(1, 5, 3))
        d = MibNode(name="d", oid=(1, 5, 1))
        root.add_child(a); root.add_child(b)
        a.add_child(c); a.add_child(d)
        root.sort_children_by_oid()
        assert [n.name for n in root.children] == ["b", "a"]
        assert [n.name for n in a.children] == ["d", "c"]

    def test_path_walks_up_to_root(self):
        r = MibNode(name="iso", oid=(1,))
        a = MibNode(name="org", oid=(1, 3))
        b = MibNode(name="dod", oid=(1, 3, 6))
        r.add_child(a); a.add_child(b)
        assert b.path() == "iso.org.dod"
        # A standalone node with no parent has just its own name.
        lone = MibNode(name="lone", oid=(1,))
        assert lone.path() == "lone"


# --- MibTree bootstrap & lookup ------------------------------------------

class TestBootstrapAndLookup:
    @pytest.fixture
    def tree(self):
        return MibTree()

    def test_bootstrap_creates_canonical_names(self, tree):
        # iso/org/dod/internet/mib-2/enterprises etc. exist after init.
        assert tree.node_by_oid((1, 3, 6, 1)) is not None
        assert tree.node_by_oid((1, 3, 6, 1)).name == "internet"
        assert tree.node_by_name("enterprises").oid == (1, 3, 6, 1, 4, 1)
        assert tree.node_by_name("mib-2").oid == (1, 3, 6, 1, 2, 1)

    def test_resolve_name_numeric(self, tree):
        assert tree.resolve_name("1.3.6.1.2.1.1.3.0") == (1, 3, 6, 1, 2, 1, 1, 3, 0)
        # Leading dot is tolerated.
        assert tree.resolve_name(".1.3.6.1") == (1, 3, 6, 1)

    def test_resolve_name_symbolic_with_suffix(self, tree):
        # mib-2 is bootstrapped at .1.3.6.1.2.1
        assert tree.resolve_name("mib-2.1.0") == (1, 3, 6, 1, 2, 1, 1, 0)

    def test_resolve_name_unknown(self, tree):
        assert tree.resolve_name("noSuchSymbol") is None
        assert tree.resolve_name("") is None

    def test_lookup_oid_returns_nearest_ancestor(self, tree):
        n = tree.lookup_oid((1, 3, 6, 1, 2, 1, 99, 99))
        assert n is not None
        assert n.name == "mib-2"   # nearest known ancestor

    def test_lookup_oid_below_root(self, tree):
        # Anything starting with .1 hits the iso root at minimum.
        assert tree.lookup_oid((1, 99, 99, 99)).name == "iso"

    def test_format_oid_with_named_node(self, tree):
        assert tree.format_oid((1, 3, 6, 1, 2, 1)) == "mib-2"

    def test_format_oid_with_suffix(self, tree):
        assert tree.format_oid((1, 3, 6, 1, 2, 1, 1, 3, 0)) == "mib-2.1.3.0"

    def test_format_oid_unknown_falls_back_to_dotted(self):
        # A fresh tree without bootstrap parents at .2.999 returns a dotted form.
        # Note we can't fully strip bootstrap; instead use a path that has no
        # ancestor at all in MibTree.
        bare = MibTree.__new__(MibTree)
        bare.root = MibNode(name="iso", oid=(1,))
        bare._by_oid = {}    # empty
        bare._by_name = {}
        bare.modules = {}
        bare.tc_enums = {}
        bare.missing_enabled = []
        assert bare.format_oid((2, 999)).startswith(".")


# --- _longest_prefix -----------------------------------------------------

def test_longest_prefix_finds_existing():
    t = MibTree()
    # mib-2 (1.3.6.1.2.1) is bootstrapped — child at (1,3,6,1,2,1,99,1)
    # has longest_prefix == (1,3,6,1,2,1).
    assert t._longest_prefix((1, 3, 6, 1, 2, 1, 99, 1)) == (1, 3, 6, 1, 2, 1)


def test_longest_prefix_falls_back_to_root():
    t = MibTree()
    # An OID under iso but with no bootstrap ancestor still resolves to (1,).
    assert t._longest_prefix((1, 99)) == (1,)


# --- is_enterprise_module / default_enabled_modules ----------------------

class TestEnterpriseDetection:
    def test_enterprise_only(self):
        data = {
            "VENDOR-MIB": {"name": "VENDOR-MIB"},
            "vendorRoot": {"oid": "1.3.6.1.4.1.99999"},
            "vendorThing": {"oid": "1.3.6.1.4.1.99999.1"},
        }
        assert MibTree.is_enterprise_module(data) is True

    def test_mixed_module_is_not_enterprise(self):
        data = {
            "vendorRoot": {"oid": "1.3.6.1.4.1.99999"},
            "stdThing":   {"oid": "1.3.6.1.2.1.99"},
        }
        assert MibTree.is_enterprise_module(data) is False

    def test_module_without_oids_is_not_enterprise(self):
        # Pure TC module — no OIDs.
        data = {
            "Foo": {"class": "textualconvention", "type": {}},
        }
        assert MibTree.is_enterprise_module(data) is False

    def test_default_enabled_skips_enterprise(self, tmp_path):
        (tmp_path / "STD-MIB.json").write_text(json.dumps({
            "stdThing": {"oid": "1.3.6.1.2.1.99"}}))
        (tmp_path / "VENDOR-MIB.json").write_text(json.dumps({
            "vendorThing": {"oid": "1.3.6.1.4.1.99999.1"}}))
        out = MibTree.default_enabled_modules(tmp_path)
        assert "STD-MIB" in out
        assert "VENDOR-MIB" not in out

    def test_default_enabled_skips_corrupt_json(self, tmp_path):
        (tmp_path / "BAD.json").write_text("{ not json")
        (tmp_path / "OK.json").write_text(json.dumps({
            "x": {"oid": "1.3.6.1.2.1.7"}}))
        out = MibTree.default_enabled_modules(tmp_path)
        assert "OK" in out
        assert "BAD" not in out


# --- load_compiled --------------------------------------------------------

def _write_module(dir_: Path, name: str, body: dict) -> None:
    (dir_ / f"{name}.json").write_text(json.dumps(body))


class TestLoadCompiled:
    def test_load_missing_directory_returns_zero(self, tmp_path):
        t = MibTree()
        assert t.load_compiled(tmp_path / "does-not-exist") == 0

    def test_load_empty_directory(self, tmp_path):
        t = MibTree()
        assert t.load_compiled(tmp_path) == 0

    def test_load_basic_module(self, tmp_path):
        _write_module(tmp_path, "TEST-MIB", {
            "testRoot": {
                "oid": "1.3.6.1.4.1.99999",
                "class": "objectidentity",
                "nodetype": "node",
            },
            "sysWidget": {
                "oid": "1.3.6.1.4.1.99999.1",
                "class": "objecttype",
                "nodetype": "scalar",
                "syntax": {"type": "Integer32"},
                "maxaccess": "read-only",
                "status": "current",
                "description": "a widget",
                "units": "widgets",
            },
        })
        t = MibTree()
        assert t.load_compiled(tmp_path) == 1
        n = t.node_by_name("sysWidget")
        assert n is not None
        assert n.module == "TEST-MIB"
        assert n.oid == (1, 3, 6, 1, 4, 1, 99999, 1)
        assert n.is_scalar
        assert n.syntax == "Integer32"
        assert n.access == "read-only"
        assert n.units == "widgets"
        # Resolution + lookup also work after load.
        assert t.resolve_name("sysWidget") == (1, 3, 6, 1, 4, 1, 99999, 1)
        assert t.lookup_oid((1, 3, 6, 1, 4, 1, 99999, 1)).name == "sysWidget"
        assert t.format_oid((1, 3, 6, 1, 4, 1, 99999, 1, 0)) == "sysWidget.0"

    def test_enabled_filter(self, tmp_path):
        _write_module(tmp_path, "MOD-A", {"a": {"oid": "1.3.6.1.2.1.99"}})
        _write_module(tmp_path, "MOD-B", {"b": {"oid": "1.3.6.1.2.1.98"}})
        t = MibTree()
        n = t.load_compiled(tmp_path, enabled=["MOD-A"])
        assert n == 1
        assert t.node_by_name("a") is not None
        assert t.node_by_name("b") is None

    def test_enabled_logs_missing(self, tmp_path, caplog):
        _write_module(tmp_path, "MOD-A", {"a": {"oid": "1.3.6.1.2.1.99"}})
        t = MibTree()
        with caplog.at_level("WARNING"):
            t.load_compiled(tmp_path, enabled=["MOD-A", "NOT-THERE"])
        assert t.missing_enabled == ["NOT-THERE"]
        assert any("enabled-but-missing" in r.message for r in caplog.records)

    def test_load_skips_corrupt_file(self, tmp_path, caplog):
        (tmp_path / "BAD.json").write_text("{ not json")
        _write_module(tmp_path, "GOOD", {"x": {"oid": "1.3.6.1.2.1.7"}})
        t = MibTree()
        with caplog.at_level("WARNING"):
            assert t.load_compiled(tmp_path) == 1
        assert t.node_by_name("x") is not None

    def test_module_with_textual_convention_inherits_enums(self, tmp_path):
        _write_module(tmp_path, "TC-MIB", {
            "MyState": {
                "class": "textualconvention",
                "name": "MyState",
                "type": {
                    "constraints": {"enumeration": {"up": 1, "down": 2}},
                },
            },
            "thing": {
                "oid": "1.3.6.1.4.1.42.1",
                "class": "objecttype",
                "nodetype": "scalar",
                "syntax": {"type": "MyState"},
            },
        })
        t = MibTree()
        t.load_compiled(tmp_path)
        n = t.node_by_name("thing")
        assert n.enum_values == {1: "up", 2: "down"}

    def test_textual_convention_alias_chain(self, tmp_path):
        # B is an alias of A; thing uses B → ends up with A's enums.
        _write_module(tmp_path, "TC-MIB", {
            "TcA": {
                "class": "textualconvention",
                "name": "TcA",
                "type": {"constraints": {"enumeration": {"on": 1}}},
            },
            "TcB": {
                "class": "textualconvention",
                "name": "TcB",
                "type": {"type": "TcA"},
            },
            "obj": {
                "oid": "1.3.6.1.4.1.42.2",
                "class": "objecttype",
                "nodetype": "scalar",
                "syntax": {"type": "TcB"},
            },
        })
        t = MibTree()
        t.load_compiled(tmp_path)
        assert t.node_by_name("obj").enum_values == {1: "on"}

    def test_inline_enums_on_objecttype(self, tmp_path):
        _write_module(tmp_path, "MIB", {
            "obj": {
                "oid": "1.3.6.1.4.1.42.3",
                "class": "objecttype",
                "nodetype": "scalar",
                "syntax": {
                    "type": "Integer32",
                    "constraints": {"enumeration": {"foo": 1, "bar": 2}},
                },
            },
        })
        t = MibTree()
        t.load_compiled(tmp_path)
        assert t.node_by_name("obj").enum_values == {1: "foo", 2: "bar"}

    def test_indices_and_implied(self, tmp_path):
        _write_module(tmp_path, "MIB", {
            "fooEntry": {
                "oid": "1.3.6.1.4.1.42.10.1",
                "class": "objecttype",
                "nodetype": "row",
                "indices": [
                    {"object": "fooIndex"},
                    {"object": "fooName", "implied": True},
                ],
            },
            "barEntry": {
                "oid": "1.3.6.1.4.1.42.11.1",
                "class": "objecttype",
                "indices": [{"object": "barIdx"}],
                # nodetype empty → derived as 'row' because indices present.
            },
        })
        t = MibTree()
        t.load_compiled(tmp_path)
        foo = t.node_by_name("fooEntry")
        assert foo.indices == ["fooIndex", "fooName"]
        assert foo.implied_last_index is True
        bar = t.node_by_name("barEntry")
        assert bar.node_role == "row"

    def test_synthesizes_intermediate_numeric_nodes(self, tmp_path):
        """An OID with no named ancestors between mib-2 and the leaf
        should auto-create numeric stubs along the path."""
        _write_module(tmp_path, "MIB", {
            "deepLeaf": {
                "oid": "1.3.6.1.4.1.99999.1.2.3.4.5",
                "class": "objecttype",
                "nodetype": "scalar",
            },
        })
        t = MibTree()
        t.load_compiled(tmp_path)
        # Stubs exist for every intermediate.
        for i in range(1, 6):
            oid = (1, 3, 6, 1, 4, 1, 99999) + tuple(range(1, i))
            assert t.node_by_oid(oid) is not None or i == 1
        leaf = t.node_by_name("deepLeaf")
        assert leaf is not None
        assert leaf.oid == (1, 3, 6, 1, 4, 1, 99999, 1, 2, 3, 4, 5)

    def test_enriches_existing_stub_with_metadata(self, tmp_path):
        """When the same OID already exists as a numeric stub (because a
        deeper sibling registered first), the second registration enriches
        rather than creating a duplicate."""
        _write_module(tmp_path, "MIB", {
            "deepLeaf": {
                "oid": "1.3.6.1.4.1.42.1.2",
                "class": "objecttype",
                "nodetype": "scalar",
            },
            "midNode": {
                "oid": "1.3.6.1.4.1.42.1",
                "class": "objectidentity",
                "description": "the named middle",
            },
        })
        t = MibTree()
        t.load_compiled(tmp_path)
        # mid-node should have the proper name (not the synthesized "1").
        n = t.node_by_oid((1, 3, 6, 1, 4, 1, 42, 1))
        assert n is not None
        assert n.name == "midNode"
        assert n.description == "the named middle"

    def test_resolves_symbolic_oid_via_parent_lookup(self, tmp_path):
        """OID stored as 'parentName.5.1' — pysmi sometimes leaves these."""
        _write_module(tmp_path, "MIB", {
            "parentNode": {
                "oid": "1.3.6.1.4.1.42",
                "class": "objectidentity",
            },
            "childNode": {
                "oid": "parentNode.5.1",   # symbolic reference
                "class": "objecttype",
                "nodetype": "scalar",
            },
        })
        t = MibTree()
        t.load_compiled(tmp_path)
        assert t.resolve_name("childNode") == (1, 3, 6, 1, 4, 1, 42, 5, 1)

    def test_skips_definitions_without_oid(self, tmp_path):
        """MIB module exports without 'oid' should be silently skipped."""
        _write_module(tmp_path, "MIB", {
            "noOid": {"class": "objecttype"},
            "withOid": {"oid": "1.3.6.1.2.1.42"},
        })
        t = MibTree()
        n = t.load_compiled(tmp_path)
        assert n == 1
        assert t.node_by_name("noOid") is None
        assert t.node_by_name("withOid") is not None

    def test_skips_unparseable_oid(self, tmp_path):
        """OID that's neither pure-numeric nor parent.suffix gets dropped."""
        _write_module(tmp_path, "MIB", {
            "bogus": {"oid": "@@invalid@@", "class": "objecttype"},
        })
        t = MibTree()
        t.load_compiled(tmp_path)
        assert t.node_by_name("bogus") is None


# --- _discover_modules ---------------------------------------------------

class TestDiscoverModules:
    def test_walks_extensions(self, tmp_path):
        (tmp_path / "FOO.mib").write_text("")
        (tmp_path / "bar.txt").write_text("")
        (tmp_path / "BAZ.my").write_text("")
        (tmp_path / "QUUX").write_text("")     # bare name, no extension
        out = _discover_modules([tmp_path])
        # Names are uppercased and de-duplicated.
        assert set(out) == {"FOO", "BAR", "BAZ", "QUUX"}

    def test_skips_stub_mibs(self, tmp_path):
        (tmp_path / "ASN1.mib").write_text("")
        (tmp_path / "OK.mib").write_text("")
        out = _discover_modules([tmp_path])
        assert "ASN1" not in out
        assert "OK" in out

    def test_missing_dir_skipped(self, tmp_path):
        out = _discover_modules([tmp_path / "does-not-exist", tmp_path])
        assert out == []

    def test_dedup_across_dirs(self, tmp_path):
        d1 = tmp_path / "d1"; d1.mkdir()
        d2 = tmp_path / "d2"; d2.mkdir()
        (d1 / "FOO.mib").write_text("")
        (d2 / "FOO.mib").write_text("")
        out = _discover_modules([d1, d2])
        assert out == ["FOO"]


# --- compile_mibs (slow integration) -------------------------------------

def test_compile_mibs_against_repo_fixtures(tmp_path):
    """Smoke-test: compile a single small MIB from the repo's mibs-src/.
    This exercises _make_compiler + the on_progress callback. Slower than
    the rest of the suite (~few seconds), but the only way to verify the
    pysmi path didn't regress."""
    # Pick the cheapest module we ship.
    src = mib_loader.config.default_mibs_src()
    if not src.exists():
        pytest.skip("repo mibs-src/ not present in this checkout")
    progress: list[tuple[str, str, int, int]] = []
    out = mib_loader.compile_mibs(
        [src], tmp_path,
        on_progress=lambda mod, status, done, total:
            progress.append((mod, status, done, total)),
    )
    assert isinstance(out, dict)
    # At least one JSON should now exist.
    jsons = list(tmp_path.glob("*.json"))
    assert jsons, "compile_mibs produced no output"
    # Progress callback fired for every module attempted.
    assert progress
    assert progress[-1][3] >= 1


def test_compile_modules_one_off(tmp_path):
    """compile_modules() takes an explicit list — test it compiles SNMPv2-MIB
    or any always-present module from the repo's mibs-src/."""
    src = mib_loader.config.default_mibs_src()
    if not src.exists():
        pytest.skip("repo mibs-src/ not present")
    # Discover one module to ask for.
    mods = _discover_modules([src])
    if not mods:
        pytest.skip("no MIBs to compile")
    target = mods[0]
    res = mib_loader.compile_modules([target], [], tmp_path)
    assert isinstance(res, dict)


# --- build_tree_with_default_mibs (smoke) --------------------------------

def test_build_tree_with_default_mibs_reuses_existing(monkeypatch, tmp_path):
    """If compiled JSONs already exist, build_tree_with_default_mibs must
    skip the compile step entirely."""
    # Synthesize a tiny pre-compiled module in tmp_path/dest.
    dest = tmp_path / "compiled"
    dest.mkdir()
    _write_module(dest, "TINY", {"x": {"oid": "1.3.6.1.2.1.7"}})

    monkeypatch.setattr(mib_loader.config, "compiled_mibs_dir", lambda: dest)
    monkeypatch.setattr(mib_loader.config, "default_mibs_src",
                         lambda: tmp_path / "src")

    called = {"compile_mibs": 0}

    def fake_compile(*a, **kw):
        called["compile_mibs"] += 1
        return {}
    monkeypatch.setattr(mib_loader, "compile_mibs", fake_compile)

    tree = mib_loader.build_tree_with_default_mibs()
    assert called["compile_mibs"] == 0    # skipped — JSONs already exist
    assert tree.node_by_name("x") is not None


def test_build_tree_with_default_mibs_compiles_when_empty(monkeypatch, tmp_path):
    """First-run path: dest is empty → compile_mibs is invoked."""
    dest = tmp_path / "compiled"
    dest.mkdir()
    src = tmp_path / "src"
    src.mkdir()

    monkeypatch.setattr(mib_loader.config, "compiled_mibs_dir", lambda: dest)
    monkeypatch.setattr(mib_loader.config, "default_mibs_src", lambda: src)

    def fake_compile(src_dirs, d, **kw):
        # Materialise one JSON so subsequent load_compiled has something.
        _write_module(d, "M", {"x": {"oid": "1.3.6.1.2.1.7"}})
        return {"M": "ok"}

    monkeypatch.setattr(mib_loader, "compile_mibs", fake_compile)
    tree = mib_loader.build_tree_with_default_mibs(rebuild=False)
    assert tree.node_by_name("x") is not None
