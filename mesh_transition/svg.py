# -*- coding: utf-8 -*-
"""SVG drawing of a quad mesh: elements, node numbers, element numbers, the worst element filled.

Plain text output, no dependency, readable in a diff. The worst element is the one with the smallest
`corner_jacobian` (quality.py) -- this project's own metric, not HyperMesh's."""
import io

from .quality import metrics, worst_element


def draw(m, path, title="", zones=None, scale=90.0, margin=46.0, node_ids=True, elem_ids=True, mark_worst=True):
    xs = [p[0] for p in m.nodes.values()]
    ys = [p[1] for p in m.nodes.values()]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    W, H = (x1 - x0) * scale + 2 * margin, (y1 - y0) * scale + 2 * margin + 26

    def X(x):
        return margin + (x - x0) * scale

    def Y(y):
        return H - margin - (y - y0) * scale          # y up

    worst = worst_element(m) if (mark_worst and m.quads) else None
    s = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" viewBox="0 0 %.0f %.0f">' % (W, H, W, H),
        '<rect width="100%%" height="100%%" fill="#ffffff"/>',
        '<text x="%.1f" y="20" font-family="monospace" font-size="14" fill="#111">%s</text>' % (margin, title),
    ]
    for q in sorted(m.quads):
        p = m.corners(q)
        pts = " ".join("%.2f,%.2f" % (X(x), Y(y)) for x, y in p)
        fill = "#f6c6c6" if q == worst else "#eaf3fb"
        s.append('<polygon points="%s" fill="%s" stroke="#1a1a1a" stroke-width="1.6"/>' % (pts, fill))
    if elem_ids:
        for q in sorted(m.quads):
            p = m.corners(q)
            cx, cy = sum(v[0] for v in p) / 4.0, sum(v[1] for v in p) / 4.0
            col = "#a11" if q == worst else "#2b6cb0"
            s.append('<text x="%.1f" y="%.1f" font-family="monospace" font-size="12" fill="%s" '
                     'text-anchor="middle">E%d</text>' % (X(cx), Y(cy) + 4, col, q))
    if node_ids:
        for i in sorted(m.nodes):
            x, y = m.nodes[i]
            s.append('<circle cx="%.2f" cy="%.2f" r="2.6" fill="#1a1a1a"/>' % (X(x), Y(y)))
            s.append('<text x="%.1f" y="%.1f" font-family="monospace" font-size="11" fill="#444" '
                     'text-anchor="middle">%d</text>' % (X(x), Y(y) - 7, i))
    if worst is not None:
        r = metrics(m.corners(worst))
        s.append('<text x="%.1f" y="%.1f" font-family="monospace" font-size="11" fill="#a11">'
                 'worst E%d: corner_jacobian %.4f, skew %.1f deg</text>'
                 % (margin, H - 12, worst, r["corner_jacobian"], r["skew_deg"]))
    s.append("</svg>")
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(s) + "\n")
    return path
