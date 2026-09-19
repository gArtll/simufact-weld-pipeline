# -*- coding: utf-8 -*-
"""Write a solid mesh as a HyperMesh-flavoured Nastran deck.

The point of matching the flavour rather than writing the minimum Nastran that parses is that the result has to open
next to the deck it replaces and look like its sibling: same card forms, same field widths, same component and
property organisation, same id bases. Only the mesh differs, which is the thing being compared.

Small field, eight characters. That is what the deck being replaced uses, and it is a real limit: a coordinate keeps
about six significant figures, so a node at -1819.13 mm is stored to the nearest hundredth of a millimetre. The
in-memory mesh is more precise than the file; both the old deck and this one lose the same digits the same way."""
import io
import math

HM_HEADER = """$$------------------------------------------------------------------------------$
$$                                                                              $
$$ NASTRAN Input Deck written by mesh_transition
$$                                                                              $
$$   Template:  NastranMSC general                                              $
$$                                                                              $
$$------------------------------------------------------------------------------$
$$------------------------------------------------------------------------------$
$$                      Executive Control Cards                                 $
$$------------------------------------------------------------------------------$
CEND
$$------------------------------------------------------------------------------$
$$                      Case Control Cards                                      $
$$------------------------------------------------------------------------------$
$$------------------------------------------------------------------------------$
$$                      Bulk Data Cards                                         $
$$------------------------------------------------------------------------------$
BEGIN BULK
$$
$$  GRID Data
$$
"""

HM_TAIL = """$$
$$------------------------------------------------------------------------------$
$$    Property Definition for 1-D Elements                                      $
$$------------------------------------------------------------------------------$
$$
$$------------------------------------------------------------------------------$
$$    HyperMesh name and color information for generic components               $
$$------------------------------------------------------------------------------$
"""


def f8(v):
    """A float in eight characters, as much precision as fits, Nastran small field."""
    v = float(v)
    if v == 0.0:
        return "     0.0"
    for p in range(9, 0, -1):
        s = "%.*g" % (p, v)
        if "e" in s:                                   # 1.234e+05 -> 1.234+5, which Nastran reads
            mant, ex = s.split("e")
            s = mant + ("+" if int(ex) >= 0 else "-") + str(abs(int(ex)))
        elif "." not in s:
            s += "."
        if len(s) <= 8:
            return "%8s" % s
    raise ValueError("%r does not fit in a small field" % v)


def i8(v):
    s = "%d" % v
    if len(s) > 8:
        raise ValueError("id %d does not fit in a small field" % v)
    return "%8s" % s


def write_deck(solid, path, comp_id=1, comp_name="component", comp_color=36, pid=0,
               node_base=1, elem_base=1, node_order=None, title=None):
    """Write `solid` as a deck. Returns {"nodes": ..., "elements": ..., "node_ids": {...}}.

    `node_base` and `elem_base` are the first ids used, so a replacement deck can start where the original did.
    Every element is given to one component through the `$HMCOMP ID` block it is written under, and to `pid` through
    its own property field, which is how the deck being replaced is organised."""
    ids = sorted(solid.nodes) if node_order is None else list(node_order)
    nmap = {i: node_base + k for k, i in enumerate(ids)}
    hexes = [(k, e) for k, e in enumerate(solid.elements) if e[0] == "hex8"]
    pents = [(k, e) for k, e in enumerate(solid.elements) if e[0] == "penta6"]
    emap, n = {}, elem_base
    for k, _ in hexes + pents:
        emap[k] = n
        n += 1
    with io.open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write(HM_HEADER if title is None else HM_HEADER.replace(
            "NASTRAN Input Deck written by mesh_transition",
            "NASTRAN Input Deck written by mesh_transition: %s" % title[:60]))
        for i in ids:
            x, y, z = solid.nodes[i]
            f.write("GRID    %s        %s%s%s\n" % (i8(nmap[i]), f8(x), f8(y), f8(z)))
        f.write("$$\n$$------------------------------------------------------------------------------$\n"
                "$$             Group Definitions                                                $\n"
                "$$------------------------------------------------------------------------------$\n")
        if hexes:
            f.write("$$\n$$  CHEXA Elements: First Order\n$$\n$HMCOMP ID %20d\n$\n" % comp_id)
            for k, (_, g) in hexes:
                v = [nmap[i] for i in g]
                f.write("CHEXA   %s%s%s%s%s%s%s%s+       \n" % tuple([i8(emap[k]), i8(pid)] + [i8(x) for x in v[:6]]))
                f.write("+       %s%s\n" % (i8(v[6]), i8(v[7])))
        if pents:
            f.write("$$\n$$  CPENTA Elements 6-noded\n$$\n$HMCOMP ID %20d\n$\n" % comp_id)
            for k, (_, g) in pents:
                v = [nmap[i] for i in g]
                f.write("CPENTA  %s%s%s%s%s%s%s%s\n" % tuple([i8(emap[k]), i8(pid)] + [i8(x) for x in v]))
        f.write(HM_TAIL)
        f.write('$HMNAME COMP %20d"%s" \n$HWCOLOR COMP %20d%8d\n$\n' % (comp_id, comp_name, comp_id, comp_color))
        f.write("$$\n$$------------------------------------------------------------------------------$\n"
                "$$    Property Definition for Surface and Volume Elements                        $\n"
                "$$------------------------------------------------------------------------------$\n"
                "$$\n$$------------------------------------------------------------------------------$\n"
                "$$                      Material Definition Cards                               $\n"
                "$$------------------------------------------------------------------------------$\n"
                "$$\n$$------------------------------------------------------------------------------$\n"
                "$$        Loads and Boundary Conditions                                         $\n"
                "$$------------------------------------------------------------------------------$\n"
                "ENDDATA\n")
    return {"nodes": len(ids), "elements": len(emap), "hex8": len(hexes), "penta6": len(pents),
            "node_id_range": (node_base, node_base + len(ids) - 1),
            "element_id_range": (elem_base, elem_base + len(emap) - 1)}


def round_trip_error(solid, path):
    """How far the written deck's nodes are from the mesh in memory: the cost of the eight character field."""
    from .face import read_solid_bdf
    nodes, _ = read_solid_bdf(path)
    ids = sorted(solid.nodes)
    got = [nodes[k] for k in sorted(nodes)]
    return max(math.dist(solid.nodes[i], p) for i, p in zip(ids, got))
