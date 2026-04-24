"""build_virt_tree — folder-chain collapse.

These tests build a minimal MibNode graph by hand, side-stepping pysmi
compilation. The invariant we care about:

  chains of folders (no access/syntax, no item role) should collapse
  into one VirtNode with a dotted display name; anything that has access
  or is a table/row/scalar/column/notification stops the chain."""
from __future__ import annotations

from pymibbrowser.mib_loader import MibNode
from pymibbrowser.ui.mib_tree_model import _is_folder, build_virt_tree


def _n(name, oid, **kw):
    node = MibNode(name=name, oid=tuple(oid))
    for k, v in kw.items():
        setattr(node, k, v)
    return node


def test_is_folder_rules():
    assert _is_folder(_n("iso", (1,))) is True
    assert _is_folder(_n("sysDescr", (1, 3, 6, 1, 2, 1, 1, 1),
                        syntax="DisplayString", access="read-only")) is False
    assert _is_folder(_n("ifTable", (1, 3, 6, 1, 2, 1, 2, 2),
                        node_role="table")) is False
    assert _is_folder(_n("trap", (1, 1, 1),
                        node_role="notification")) is False


def test_chain_of_folders_collapses():
    # root=a → b → c (all folders, single child). Last node has 2 kids
    # so chain stops there.
    a = _n("a", (1,))
    b = _n("b", (1, 3))
    c = _n("c", (1, 3, 6))
    d = _n("d", (1, 3, 6, 1), syntax="Integer", access="read-only")
    e = _n("e", (1, 3, 6, 2), syntax="Integer", access="read-only")
    a.add_child(b); b.add_child(c); c.add_child(d); c.add_child(e)

    phantom, _oid_map = build_virt_tree(a)
    # phantom has one visible top: the collapsed "a.b.c"
    assert len(phantom.children) == 1
    top = phantom.children[0]
    assert top.display == "a.b.c"
    assert top.real is c   # tail of the chain
    # d and e stay separate children (each is a scalar → item, not folder)
    assert len(top.children) == 2
    assert {ch.display for ch in top.children} == {"d", "e"}


def test_chain_stops_at_table():
    # a (folder) → atTable (table) → atEntry (row) → cols. Tables and
    # rows aren't folders, so each becomes its own node.
    root = _n("a", (1,))
    tbl = _n("atTable", (1, 1), node_role="table")
    row = _n("atEntry", (1, 1, 1), node_role="row")
    c1 = _n("atIfIndex", (1, 1, 1, 1),
            syntax="Integer", node_role="column", access="read-only")
    c2 = _n("atPhys", (1, 1, 1, 2),
            syntax="OctetString", node_role="column", access="read-only")
    root.add_child(tbl); tbl.add_child(row)
    row.add_child(c1); row.add_child(c2)

    phantom, _oid_map = build_virt_tree(root)
    top = phantom.children[0]
    assert top.display == "a"            # can't merge into the table
    assert len(top.children) == 1
    assert top.children[0].display == "atTable"
    assert top.children[0].children[0].display == "atEntry"


def test_oid_map_covers_chain_middles(tmp_path=None):
    # build_virt_tree must record every *mid-chain* OID pointing at
    # the collapsed VirtNode so find_index works for them too.
    root = _n("a", (1,))
    b = _n("b", (1, 3))
    c = _n("c", (1, 3, 6))
    d1 = _n("d1", (1, 3, 6, 1),
            syntax="Integer", access="read-only")
    d2 = _n("d2", (1, 3, 6, 2),
            syntax="Integer", access="read-only")
    root.add_child(b); b.add_child(c); c.add_child(d1); c.add_child(d2)

    phantom, oid_map = build_virt_tree(root)
    top = phantom.children[0]
    # All three OIDs in the collapsed "a.b.c" chain should resolve to
    # the same VirtNode.
    for oid in [(1,), (1, 3), (1, 3, 6)]:
        assert oid_map[oid] is top


def test_single_root_false_hides_collapsed_chain():
    root = _n("a", (1,))
    b = _n("b", (1, 3))
    c = _n("c", (1, 3, 6))
    c.add_child(_n("d", (1, 3, 6, 1), syntax="Integer",
                    access="read-only"))
    c.add_child(_n("e", (1, 3, 6, 2), syntax="Integer",
                    access="read-only"))
    root.add_child(b); b.add_child(c)

    phantom, _ = build_virt_tree(root, single_root=False)
    # Children of the would-be collapsed top bubble up directly.
    names = {ch.display for ch in phantom.children}
    assert names == {"d", "e"}
