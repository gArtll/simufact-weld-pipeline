# -*- coding: utf-8 -*-
"""A text label under the part: the repository's URL and "not reviewed by a person", in pixel letters.

The label is an STL surface that `build` puts into the Simufact project as a bearing with no material and no
temperature, standing off the part by `gap_mm`. Nothing touches it, so it carries no load and no heat; it is there to be
seen in the model and result views, and so in every screenshot taken of them. It is a reminder, not a lock: it can be
hidden in Simufact or switched off in the case (`watermark.enabled: false`), which is the point -- whoever has
checked the results switches it off, whoever has not leaves it in.

Placement follows the screen, not the part. Screenshots are taken from Simufact's views, and the home view is fixed to
the global axes whatever way the part lies (global Z up the screen, the camera on the -Y side). A label laid out in the
joint frame was seen edge-on there; this one faces the camera like a caption:
    w       `view`, towards the camera (default -Y); the letters face it
    v       `screen_up` made perpendicular to w (default +Z)
    u       v x w, left to right on the screen
It sits `gap_mm` below the part on the screen, centred under it, level with the part's front.

The default text is a baked bitmap (build/watermark_bitmap.py, tools/make_watermark_bitmap.py): no font, no Pillow, and
the same label on every machine. Other text is rendered with Pillow at build time."""
import hashlib
import os
import struct

import numpy as np

DEFAULT_LINES = ("github.com/gArtll/simufact-weld-pipeline", "自动生成  未经人工核对")
PX_PER_LINE = 22                  # raster height of one text line
LINE_PITCH = 1.3                  # line spacing, in line heights
_WINFONTS = os.path.join(os.environ.get("WINDIR", ""), "Fonts")
FONT_CANDIDATES = (os.path.join(_WINFONTS, "msyh.ttc"), os.path.join(_WINFONTS, "simhei.ttf"),
                   "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                   "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
                   "/System/Library/Fonts/PingFang.ttc")
DEFAULTS = {"line_height_mm": 14.0, "thickness_mm": 0.0, "gap_mm": 30.0, "max_width_fraction": 0.8,
            "view": (0.0, -1.0, 0.0), "screen_up": (0.0, 0.0, 1.0)}


# ---------------------------------------------------------------- text -> bitmap
def render_bitmap(lines, font=None):
    """Rows of booleans (top row first) for `lines`, rendered with Pillow; and the font file used."""
    from PIL import Image, ImageDraw, ImageFont
    font = font or next((f for f in FONT_CANDIDATES if os.path.isfile(f)), None)
    if not font:
        raise RuntimeError("no CJK font found for the watermark text; give watermark.font")
    ft = ImageFont.truetype(font, PX_PER_LINE)
    step = int(PX_PER_LINE * LINE_PITCH)
    im = Image.new("L", (max(int(ft.getlength(s)) for s in lines) + 4, step * len(lines) + 4), 0)
    d = ImageDraw.Draw(im)
    for i, s in enumerate(lines):
        d.text((2, 2 + i * step), s, font=ft, fill=255)
    a = np.array(im) > 110
    r, c = np.where(a)
    a = a[r.min():r.max() + 1, c.min():c.max() + 1]
    return [list(map(bool, row)) for row in a], font


def default_bitmap():
    from watermark_bitmap import ROWS, WIDTH
    return [[c == "1" for c in bin(int(h, 16))[2:].zfill(len(h) * 4)[:WIDTH]] for h in ROWS]


def bitmap(lines=None, font=None):
    """(bitmap with row 0 at the bottom, source). The baked default unless other text or a font is asked for."""
    lines = tuple(lines or DEFAULT_LINES)
    if lines == DEFAULT_LINES and not font:
        rows, src = default_bitmap(), "build/watermark_bitmap.py"
    else:
        rows, src = render_bitmap(lines, font)
    return np.array(rows[::-1], dtype=bool), src


# ---------------------------------------------------------------- bitmap -> closed surface
def rects(a):
    """Lit pixels merged into rectangles (c0, r0, c1, r1): runs along a row, grown upwards while identical."""
    used = np.zeros_like(a)
    out = []
    H, W = a.shape
    for r in range(H):
        c = 0
        while c < W:
            if not a[r, c] or used[r, c]:
                c += 1
                continue
            c1 = c
            while c1 < W and a[r, c1] and not used[r, c1]:
                c1 += 1
            r1 = r + 1
            while r1 < H and a[r1, c:c1].all() and not used[r1, c:c1].any():
                r1 += 1
            used[r:r1, c:c1] = True
            out.append((c, r, c1, r1))
            c = c1
    return out


def _wall_runs(a):
    """Boundary edges of the lit region, merged into straight runs: ((u0, v0), (u1, v1)) in pixels, oriented so the lit
    side is on the left -- the outward wall normal is then (dv, -du)."""
    p = np.pad(a, 1)
    H, W = a.shape
    runs = []
    for r in range(H + 1):                       # horizontal edges on the line v = r
        below, above = p[r, 1:-1], p[r + 1, 1:-1]
        for mask, d in ((above & ~below, 1), (below & ~above, -1)):   # lit above: wall faces down, run goes +u
            c = 0
            while c < W:
                if not mask[c]:
                    c += 1
                    continue
                c1 = c
                while c1 < W and mask[c1]:
                    c1 += 1
                runs.append(((c, r), (c1, r)) if d == 1 else ((c1, r), (c, r)))
                c = c1
    for c in range(W + 1):                       # vertical edges on the line u = c
        left, right = p[1:-1, c], p[1:-1, c + 1]
        for mask, d in ((right & ~left, 1), (left & ~right, -1)):     # lit right: wall faces -u, run goes -v
            r = 0
            while r < H:
                if not mask[r]:
                    r += 1
                    continue
                r1 = r
                while r1 < H and mask[r1]:
                    r1 += 1
                runs.append(((c, r1), (c, r)) if d == 1 else ((c, r), (c, r1)))
                r = r1
    return runs


def triangles(a, pitch, thick):
    """Triangles (n, 3, 3) in label coordinates (u, v, w), the letter faces at w = thick facing +w. With thick > 0 a
    closed shell with outward normals; with thick = 0 the faces alone -- a third of the facets, and Simufact draws
    every facet edge, so fewer facets read cleaner."""
    tris = []
    for c0, r0, c1, r1 in rects(a):
        u0, v0, u1, v1 = c0 * pitch, r0 * pitch, c1 * pitch, r1 * pitch
        top = [(u0, v0, thick), (u1, v0, thick), (u1, v1, thick), (u0, v1, thick)]
        bot = [(u0, v0, 0.0), (u0, v1, 0.0), (u1, v1, 0.0), (u1, v0, 0.0)]
        for q in ((top, bot) if thick > 0 else (top,)):
            tris += [(q[0], q[1], q[2]), (q[0], q[2], q[3])]
    if thick <= 0:
        return np.array(tris, dtype=float)
    for (a0, b0), (a1, b1) in _wall_runs(a):
        p0, p1 = (a0 * pitch, b0 * pitch), (a1 * pitch, b1 * pitch)
        q = [(p0[0], p0[1], 0.0), (p1[0], p1[1], 0.0), (p1[0], p1[1], thick), (p0[0], p0[1], thick)]
        tris += [(q[0], q[1], q[2]), (q[0], q[2], q[3])]
    return np.array(tris, dtype=float)


# ---------------------------------------------------------------- placement on the screen
def _unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n < 1e-12:
        raise ValueError("watermark placement: zero direction")
    return v / n


def screen_axes(view, screen_up):
    """(u, v, w): left to right on the screen, up the screen, towards the camera."""
    w = _unit(view)
    v = np.asarray(screen_up, float)
    v = v - np.dot(v, w) * w
    if np.linalg.norm(v) < 1e-6:
        raise ValueError("watermark: screen_up %s is along view %s" % (screen_up, view))
    v = _unit(v)
    return np.cross(v, w), v, w


def place(local, part_pts, axes, width, height, gap):
    """Local (u, v, w) triangles moved under the part on the screen. Returns (world triangles, origin)."""
    pts = np.asarray(part_pts, float)
    u, v, w = axes
    pu, pv, pw = pts @ u, pts @ v, pts @ w
    origin = (0.5 * (pu.min() + pu.max()) - 0.5 * width) * u + (pv.min() - gap - height) * v + pw.max() * w
    return local @ np.array(axes) + origin, origin


def write_stl(path, tris, name="watermark"):
    """Binary STL, mm. Returns its sha256."""
    buf = bytearray(name.encode("ascii")[:80].ljust(80, b"\0"))
    buf += struct.pack("<I", len(tris))
    for t in tris:
        nrm = np.cross(t[1] - t[0], t[2] - t[0])
        nrm = nrm / (np.linalg.norm(nrm) or 1.0)
        buf += struct.pack("<12fH", *nrm, *t[0], *t[1], *t[2], 0)
    with open(path, "wb") as f:
        f.write(buf)
    return hashlib.sha256(bytes(buf)).hexdigest()


def build_label(out_stl, part_nodes, cfg=None):
    """Write the label STL for a part given by its node coordinates (mm). Returns what the manifest records."""
    cfg = dict(DEFAULTS, **{k: v for k, v in (cfg or {}).items() if v is not None})
    a, src = bitmap(cfg.get("text"), cfg.get("font"))
    axes = screen_axes(cfg["view"], cfg["screen_up"])
    pts = np.asarray(part_nodes, float)
    span = max(float((pts @ x).max() - (pts @ x).min()) for x in axes[:2])   # the part's size on the screen
    lines = len(cfg.get("text") or DEFAULT_LINES)
    pitch = cfg["line_height_mm"] * lines / a.shape[0]
    if a.shape[1] * pitch > cfg["max_width_fraction"] * span:            # no wider than the part looks
        pitch = cfg["max_width_fraction"] * span / a.shape[1]
    width, height = a.shape[1] * pitch, a.shape[0] * pitch
    local = triangles(a, pitch, cfg["thickness_mm"])
    world, origin = place(local, pts, axes, width, height, cfg["gap_mm"])
    sha = write_stl(out_stl, world)
    return {"stl": out_stl, "sha256": sha, "bitmap": src, "text": list(cfg.get("text") or DEFAULT_LINES),
            "size_mm": [round(width, 3), round(height, 3)], "triangles": int(len(world)), "gap_mm": cfg["gap_mm"],
            "view": list(cfg["view"]), "screen_up": list(cfg["screen_up"]),
            "origin": [round(float(x), 4) for x in origin], "axes": [[round(float(x), 6) for x in r] for r in axes]}
