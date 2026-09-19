# -*- coding: utf-8 -*-
"""The two reference transition templates, and strips built from them.

Why T2 has no single-cell template
----------------------------------
A simply connected all-quadrilateral region satisfies `elements = boundary_edges / 2 + interior_nodes - 1`, so its
boundary edge count must be even. One cell that turns `c` coarse edges into `f` fine ones has
`boundary_edges = c + f + 2` (two sides). For one 3->1 cell that is 1 + 3 + 2 = 6, even: a template exists. For one
2->1 cell it is 1 + 2 + 2 = 5, odd: **no all-quad filling exists at all**, for any interior node count. Two adjacent
2->1 cells give 2 + 4 + 2 = 8, even. That is why the 2->1 atom is a pair, and why the reference figures note that
the number of 2->1 cells must be even.

The same count also settles which neighbours a 2->1 cell may pair with: a 2->1 next to a 3->1 gives 2 + 5 + 2 = 9,
odd, so a lone 2->1 cannot borrow from a 3->1 neighbour. `can_build` reports this for any requested sequence.

Shape of both templates
-----------------------
One quadrilateral per coarse edge along the bottom, one per fine edge along the top, and one interior node under
each fine node except the two at the ends. T3 is 4 quads over 2 interior nodes, T2_pair is 6 over 3.

These are the smallest fillings without a flat corner *that have been found*, not a proved minimum. For T2_pair the
argument is a count: with one interior node the element budget forces some element to take two consecutive fine
edges, which puts a 180 degree corner on the fine boundary. For T3 the smaller arrangements that were tried all end
the same way, but no exhaustive enumeration has been done. Treat "smallest" as "smallest known" until one is.

Geometry knobs (fixed here, swept in round 2 of the plan): `height` of the transition row and `split`, the fraction
of that height at which the interior node row sits."""
from .mesh import Mesh

T2, T3_RATIO = 2, 3


def t3(h=1.0, height=None, split=0.5, x0=0.0, y0=0.0):
    """One 3 -> 1 cell: 3 fine edges of length `h` on top, 1 coarse edge of length 3h below. 4 quads, 2 interior."""
    height = 2.0 * h if height is None else height
    y1 = y0 + split * height
    m = Mesh()
    b0, b1 = m.node(x0, y0), m.node(x0 + 3 * h, y0)
    c1, c2 = m.node(x0 + h, y1), m.node(x0 + 2 * h, y1)
    t0, t1, t2, t3_ = [m.node(x0 + k * h, y0 + height) for k in range(4)]
    m.quad(b0, b1, c2, c1)                     # the coarse edge, tapered to one fine width
    m.quad(b0, c1, t1, t0)
    m.quad(c1, c2, t2, t1)
    m.quad(c2, b1, t3_, t2)
    return m


def t2_pair(h=1.0, height=None, split=0.5, x0=0.0, y0=0.0):
    """Two adjacent 2 -> 1 cells: 4 fine edges on top, 2 coarse edges of length 2h below. 6 quads, 3 interior."""
    height = 2.0 * h if height is None else height
    y1 = y0 + split * height
    m = Mesh()
    b0, b1, b2 = [m.node(x0 + k * 2 * h, y0) for k in range(3)]
    c1, c2, c3 = [m.node(x0 + k * h, y1) for k in (1, 2, 3)]
    t = [m.node(x0 + k * h, y0 + height) for k in range(5)]
    m.quad(b0, b1, c2, c1)                     # left coarse edge
    m.quad(b1, b2, c3, c2)                     # right coarse edge
    m.quad(b0, c1, t[1], t[0])
    m.quad(c1, c2, t[2], t[1])
    m.quad(c2, c3, t[3], t[2])
    m.quad(c3, b2, t[4], t[3])
    return m


TEMPLATES = {"T3": (t3, 3, 1), "T2_pair": (t2_pair, 4, 2)}          # name -> (builder, fine edges, coarse edges)


def can_build(cells):
    """Can a run of `cells` (each 2 or 3, meaning the fine edges it absorbs) be filled with quads?

    Returns (ok, reason). Parity is necessary for any filling; the reference templates additionally need the run to
    decompose into T3 cells and *adjacent* T2 pairs."""
    n2, n3 = cells.count(2), cells.count(3)
    b = len(cells) + sum(cells) + 2
    if b % 2:
        return False, ("boundary edge count %d is odd (Ncoarse %d + Nfine %d + 2): no all-quad filling exists, "
                       "whatever the interior looks like" % (b, len(cells), sum(cells)))
    if n2 % 2:
        return False, "%d cells of 2->1 is odd; they only close in pairs" % n2
    run, i = [], 0
    while i < len(cells):
        if cells[i] == 3:
            run.append("T3")
            i += 1
        elif i + 1 < len(cells) and cells[i] == 2 and cells[i + 1] == 2:
            run.append("T2_pair")
            i += 2
        else:
            return False, ("a 2->1 cell at position %d has no adjacent 2->1 to pair with; parity allows the strip as "
                           "a whole, but the reference templates do not cover it" % i)
    return True, run


def strip(cells, h=1.0, height=None, split=0.5):
    """A transition row built from the reference templates, welded on their shared vertical edges.

    `cells` is the sequence of coarse cells left to right, each 2 or 3. Returns (mesh, zones) where `zones` maps an
    element id to the template instance it came from."""
    ok, res = can_build(list(cells))
    if not ok:
        raise ValueError("cannot build %s: %s" % (list(cells), res))
    height = 2.0 * h if height is None else height
    out, zones, x = Mesh(), {}, 0.0
    for k, name in enumerate(res):
        build, fine, _ = TEMPLATES[name]
        piece = build(h=h, height=height, split=split)
        first = len(out.quads) + 1
        out.offset(piece, dx=x)
        for q in range(first, len(out.quads) + 1):
            zones[q] = "%s#%d" % (name, k + 1)
        x += fine * h
    return out, zones

def solve_mix(n_fine, n_coarse):
    """How many 3->1 and 2->1 cells turn `n_fine` fine edges into `n_coarse` coarse ones.

    From 2*n2 + 3*n3 = n_fine and n2 + n3 = n_coarse: n3 = n_fine - 2*n_coarse, n2 = 3*n_coarse - n_fine. Both must
    be non-negative, so the ratio has to lie between 2 and 3, and n2 must be even for the row to close at all."""
    n3, n2 = n_fine - 2 * n_coarse, 3 * n_coarse - n_fine
    ok = n2 >= 0 and n3 >= 0 and n2 % 2 == 0
    why = ""
    if n2 < 0 or n3 < 0:
        why = "ratio %.3f is outside 2..3, one transition row cannot do it" % (n_fine / float(n_coarse))
    elif n2 % 2:
        why = "n2 = %d is odd; 2->1 cells only close in pairs" % n2
    return {"n2": n2, "n3": n3, "ok": ok, "why": why, "ratio": n_fine / float(n_coarse)}


def choose_divisions(length, fine_size, coarse_size, search=6):
    """Pick n_fine and n_coarse near the target sizes so that a legal mixed row exists, closest sizes first.

    The target sizes are targets, not constraints (plan section 6): the integer topology comes first, then the sizes
    are as close to what was asked as the integers allow."""
    nf0, nc0 = max(1, int(round(length / fine_size))), max(1, int(round(length / coarse_size)))
    out = []
    for nf in range(max(1, nf0 - search), nf0 + search + 1):
        for nc in range(max(1, nc0 - search), nc0 + search + 1):
            r = solve_mix(nf, nc)
            if not r["ok"]:
                continue
            hf, hc = length / float(nf), length / float(nc)
            err = abs(hf - fine_size) / fine_size + abs(hc - coarse_size) / coarse_size
            out.append({"n_fine": nf, "n_coarse": nc, "n2": r["n2"], "n3": r["n3"],
                        "fine_size": hf, "coarse_size": hc, "size_error": err, "ratio": r["ratio"]})
    return sorted(out, key=lambda d: d["size_error"])


def layout(n2, n3, order="interleaved"):
    """The left-to-right sequence of cells. T2 cells always come out adjacent, in pairs."""
    pairs, threes = n2 // 2, n3
    if order == "grouped":
        seq = ["T2_pair"] * pairs + ["T3"] * threes
    elif order == "interleaved":
        seq, a, b = [], pairs, threes
        while a or b:
            if a and (not b or a * 1.0 / max(pairs, 1) >= b * 1.0 / max(threes, 1)):
                seq.append("T2_pair"); a -= 1
            else:
                seq.append("T3"); b -= 1
    elif order == "symmetric":
        half = layout(2 * (pairs // 2), threes // 2, "interleaved")
        mid = (["T2_pair"] if pairs % 2 else []) + (["T3"] if threes % 2 else [])
        seq = half + mid + half[::-1]
    else:
        raise ValueError("unknown order %r" % order)
    cells = []
    for name in seq:
        cells += [2, 2] if name == "T2_pair" else [3]
    return cells
