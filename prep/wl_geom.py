# -*- coding: utf-8 -*-
"""Mesh-face helpers copied from the user's HyperMesh weld-line script wl.py (read-only source; functions unchanged
except comments translated), so the package no longer imports from the user's script directory."""
import collections
import math

HEX_FACES = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def plane_quads(G, E, X, tol=1e-3):
    """Hexa faces whose 4 nodes all lie on the plane x = X."""
    out = []
    for e in E:
        if len(e) != 8:
            continue
        for f in HEX_FACES:
            ns = [e[k] for k in f]
            if all(abs(G[n][0] - X) < tol for n in ns):
                out.append(ns)
    return out


def plane_quads_axis(G, E, axis, value, tol=1e-3):
    """Hexa faces whose 4 nodes lie on x/y/z = value."""
    idx = {"x": 0, "y": 1, "z": 2}[axis]
    out = []
    for e in E:
        if len(e) != 8:
            continue
        for f in HEX_FACES:
            ns = [e[k] for k in f]
            if all(abs(G[n][idx] - value) < tol for n in ns):
                out.append(ns)
    return out


def boundary_chain(quads):
    """Edges used by exactly one quad (boundary edges)."""
    cnt = collections.Counter()
    for q in quads:
        for k in range(4):
            a, b = q[k], q[(k + 1) % 4]
            cnt[(min(a, b), max(a, b))] += 1
    return [e for e, c in cnt.items() if c == 1]


def chain_order(edges, close=False):
    """Chain boundary edges into node sequences."""
    adj = collections.defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    seen, chains = set(), []
    ends = [n for n, v in adj.items() if len(v) == 1]
    starts = ends if ends else list(adj)
    for s in starts:
        if s in seen:
            continue
        ch, cur, prev = [s], s, None
        seen.add(s)
        closed = close and not ends
        while True:
            nxt = [n for n in adj[cur] if n != prev and n not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            ch.append(cur)
            seen.add(cur)
        if closed and len(ch) > 2 and ch[0] in adj[ch[-1]]:
            ch.append(ch[0])
        if len(ch) > 2:
            chains.append(ch)
    return chains


def resample(pts, n):
    """Resample a polyline to n points by arc length."""
    d = [0.0]
    for i in range(1, len(pts)):
        d.append(d[-1] + math.dist(pts[i - 1], pts[i]))
    total, out = d[-1], []
    for i in range(n):
        t = total * i / (n - 1)
        j = 0
        while j < len(d) - 2 and d[j + 1] < t:
            j += 1
        s = (t - d[j]) / (d[j + 1] - d[j]) if d[j + 1] > d[j] else 0
        out.append(tuple(pts[j][k] + s * (pts[j + 1][k] - pts[j][k]) for k in range(3)))
    return out, total


def plane_quads_dir(G, E, normal, value, tol=1e-3):
    """Hexa faces whose 4 nodes lie on the plane normal . p = value (prep/frame.py). For a coordinate axis this is
    plane_quads / plane_quads_axis exactly."""
    from prep.frame import coord
    out = []
    for e in E:
        if len(e) != 8:
            continue
        for f in HEX_FACES:
            ns = [e[k] for k in f]
            if all(abs(coord(G[n], normal) - value) < tol for n in ns):
                out.append(ns)
    return out
