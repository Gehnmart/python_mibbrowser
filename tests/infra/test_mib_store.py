"""MibTreeStore: catalogue management + dynamic resolver across rebuilds.

The MibTree-backed store is exercised against synthetic compiled JSON
written to tmp_path — same shape pysmi produces, no real MIB compile."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pymibbrowser.engine.model import MibNodeView
from pymibbrowser.infra.adapters import MibTreeStore


# --- helpers --------------------------------------------------------------

def _write_module(d: Path, name: str, body: dict) -> None:
    (d / f"{name}.json").write_text(json.dumps(body))


@pytest.fixture
def two_modules(tmp_path):
    """A standard module + a vendor module so set_enabled() has options."""
    _write_module(tmp_path, "STD-MIB", {
        "stdRoot": {
            "oid": "1.3.6.1.2.1.99",
            "class": "objectidentity",
        },
        "stdScalar": {
            "oid": "1.3.6.1.2.1.99.1",
            "class": "objecttype",
            "nodetype": "scalar",
            "syntax": {"type": "Integer32"},
            "maxaccess": "read-only",
            "description": "the standard scalar",
        },
    })
    _write_module(tmp_path, "VENDOR-MIB", {
        "vendorThing": {
            "oid": "1.3.6.1.4.1.99999.1",
            "class": "objecttype",
            "nodetype": "scalar",
            "syntax": {"type": "OCTET STRING"},
        },
    })
    return tmp_path


# --- catalogue listing ----------------------------------------------------

class TestCatalogue:
    def test_available_lists_jsons_alphabetically(self, two_modules):
        s = MibTreeStore(two_modules)
        assert s.available_modules() == ["STD-MIB", "VENDOR-MIB"]

    def test_available_empty_for_missing_directory(self, tmp_path):
        s = MibTreeStore(tmp_path / "nope")
        assert s.available_modules() == []

    def test_enabled_initially_loads_everything(self, two_modules):
        """No `enabled=` filter at construction → all modules merged."""
        s = MibTreeStore(two_modules)
        assert s.enabled_modules() == ["STD-MIB", "VENDOR-MIB"]

    def test_enabled_respects_constructor_filter(self, two_modules):
        s = MibTreeStore(two_modules, enabled=["STD-MIB"])
        assert s.enabled_modules() == ["STD-MIB"]


# --- resolver -------------------------------------------------------------

class TestResolver:
    def test_resolves_symbolic_after_load(self, two_modules):
        s = MibTreeStore(two_modules)
        r = s.resolver()
        assert r.resolve("stdScalar") == (1, 3, 6, 1, 2, 1, 99, 1)

    def test_resolves_numeric_with_leading_dot(self, two_modules):
        r = MibTreeStore(two_modules).resolver()
        assert r.resolve(".1.3.6.1.2.1.99.1") == (1, 3, 6, 1, 2, 1, 99, 1)

    def test_returns_none_for_unknown(self, two_modules):
        r = MibTreeStore(two_modules).resolver()
        assert r.resolve("noSuchSymbol") is None

    def test_resolver_is_dynamic_across_set_enabled(self, two_modules):
        """Capturing resolver(), then disabling the module behind it,
        leaves the resolver returning None for the now-removed name —
        no stale data."""
        s = MibTreeStore(two_modules)
        r = s.resolver()
        assert r.resolve("vendorThing") == (1, 3, 6, 1, 4, 1, 99999, 1)
        s.set_enabled(["STD-MIB"])
        # Same resolver instance, fresh tree — vendorThing is gone.
        assert r.resolve("vendorThing") is None
        # But stdScalar is still there.
        assert r.resolve("stdScalar") == (1, 3, 6, 1, 2, 1, 99, 1)


# --- set_enabled ----------------------------------------------------------

class TestSetEnabled:
    def test_replaces_tree_with_subset(self, two_modules):
        s = MibTreeStore(two_modules)
        assert sorted(s.enabled_modules()) == ["STD-MIB", "VENDOR-MIB"]
        s.set_enabled(["STD-MIB"])
        assert s.enabled_modules() == ["STD-MIB"]

    def test_filters_unknown_modules(self, two_modules):
        """Modules not in available_modules() are silently dropped —
        callers can't smuggle ghosts into the tree."""
        s = MibTreeStore(two_modules)
        s.set_enabled(["STD-MIB", "GHOST-MODULE"])
        assert s.enabled_modules() == ["STD-MIB"]

    def test_empty_disables_everything(self, two_modules):
        s = MibTreeStore(two_modules)
        s.set_enabled([])
        assert s.enabled_modules() == []
        # The bootstrap names (iso, mib-2, ...) survive — they're not
        # modules, they're synthetic anchors for the OID tree.
        assert s.resolver().resolve("iso") == (1,)


# --- find_node ------------------------------------------------------------

class TestFindNode:
    def test_returns_view_with_metadata(self, two_modules):
        s = MibTreeStore(two_modules)
        view = s.find_node((1, 3, 6, 1, 2, 1, 99, 1))
        assert isinstance(view, MibNodeView)
        assert view.name == "stdScalar"
        assert view.oid == (1, 3, 6, 1, 2, 1, 99, 1)
        assert view.module == "STD-MIB"
        assert view.syntax == "Integer32"
        assert view.access == "read-only"
        assert view.description == "the standard scalar"

    def test_nearest_ancestor_for_unmatched_oid(self, two_modules):
        s = MibTreeStore(two_modules)
        # .1.3.6.1.2.1.99.1.5 — beyond stdScalar; nearest is stdScalar.
        view = s.find_node((1, 3, 6, 1, 2, 1, 99, 1, 5))
        assert view is not None
        assert view.name == "stdScalar"

    def test_root_iso_resolves(self, tmp_path):
        s = MibTreeStore(tmp_path)
        view = s.find_node((1,))
        assert view is not None
        assert view.name == "iso"

    def test_returns_view_not_internal_node(self, two_modules):
        """The view is read-only and contains no MibNode internals — no
        children, no parent pointers; safe to hand to a CLI/web caller
        without leaking infra types."""
        s = MibTreeStore(two_modules)
        view = s.find_node((1, 3, 6, 1, 2, 1, 99, 1))
        assert not hasattr(view, "children")
        assert not hasattr(view, "parent")


# --- pre-built tree shortcut ---------------------------------------------

def test_construct_from_existing_tree(two_modules):
    """If the caller already loaded a tree (e.g. main() built one before
    showing the UI), the store can wrap it without re-parsing JSON."""
    from pymibbrowser.infra.mib_loader import MibTree
    pre = MibTree()
    pre.load_compiled(two_modules, enabled=["STD-MIB"])
    s = MibTreeStore(two_modules, tree=pre)
    # Nothing reloaded — enabled reflects what `pre` had.
    assert s.enabled_modules() == ["STD-MIB"]
    # The .tree property exposes the original instance.
    assert s.tree is pre


def test_tree_property_for_ui_callers(two_modules):
    """Concrete tree access — UI keeps using full MibTree API. Not part
    of MibStore Protocol; just an infra extension."""
    s = MibTreeStore(two_modules)
    from pymibbrowser.infra.mib_loader import MibTree
    assert isinstance(s.tree, MibTree)
