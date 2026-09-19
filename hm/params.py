# -*- coding: utf-8 -*-
"""case.json base_mesh block: defaults, validation, derived zone layout.

Frame (same as the web-on-plate joint type): plate top surface y = 0 (flat) or the arc below, plate width along X
centred on x = 0, plate length along Z from 0; web normal X, web centred on x = 0, standing on the plate (y >= 0).
Curvature: optional radius about an axis parallel to X, so the plate bends along its length; web nodes follow the
plate surface (y offset by the same surface height), keeping the web bottom face on the plate top face."""
import copy
import math

# prep/wl_v2.py marks a web boundary edge as weld edge when both nodes lie within 5 mm of a plate node. The first
# web element row must therefore be at least this high, or the short web end edges join the weld root line.
WEB_FIRST_ROW_MIN_MM = 5.0

DEFAULTS = {
    "plate": {"length_mm": None, "width_mm": None, "thickness_mm": None, "curvature_radius_mm": 0.0},
    "web": {"height_mm": None, "thickness_mm": None, "z_start_mm": 0.0, "length_mm": None},
    "mesh": {"weld_band_width_mm": None, "weld_band_size_mm": None, "transition_ratio": 2.0, "far_size_mm": None,
             "plate_layers": 3, "web_layers": 2},
    "quality": {"max_penta_fraction": 0.02, "min_scaled_jacobian": 0.3, "shrink_step_mm": 0.5, "max_retries": 3},
}


class ParamError(ValueError):
    pass


def normalize(block):
    """Defaults merged into the case block; raises ParamError on missing or inconsistent values."""
    p = copy.deepcopy(DEFAULTS)
    for grp, vals in (block or {}).items():
        if grp not in p:
            raise ParamError("base_mesh: unknown group %r" % grp)
        for k, v in vals.items():
            if k not in p[grp]:
                raise ParamError("base_mesh.%s: unknown field %r" % (grp, k))
            p[grp][k] = v
    missing = ["%s.%s" % (g, k) for g, vals in p.items() for k, v in vals.items() if v is None]
    if missing:
        raise ParamError("base_mesh: missing %s" % ", ".join(missing))
    pl, w, m, q = p["plate"], p["web"], p["mesh"], p["quality"]
    for g, k in (("plate", "length_mm"), ("plate", "width_mm"), ("plate", "thickness_mm"), ("web", "height_mm"),
                 ("web", "thickness_mm"), ("web", "length_mm"), ("mesh", "weld_band_width_mm"), ("mesh", "weld_band_size_mm"),
                 ("mesh", "far_size_mm"), ("quality", "shrink_step_mm")):
        if not float(p[g][k]) > 0:
            raise ParamError("base_mesh.%s.%s must be > 0" % (g, k))
    if float(m["transition_ratio"]) < 1.0:
        raise ParamError("base_mesh.mesh.transition_ratio must be >= 1")
    if int(m["plate_layers"]) < 1 or int(m["web_layers"]) < 1 or int(q["max_retries"]) < 0:
        raise ParamError("base_mesh: layers >= 1, max_retries >= 0")
    if float(w["z_start_mm"]) < 0 or float(w["z_start_mm"]) + float(w["length_mm"]) > float(pl["length_mm"]) + 1e-9:
        raise ParamError("base_mesh.web: z_start_mm + length_mm must lie within plate.length_mm")
    if float(w["thickness_mm"]) >= float(pl["width_mm"]):
        raise ParamError("base_mesh.web.thickness_mm must be smaller than plate.width_mm")
    r = float(pl["curvature_radius_mm"])
    if r < 0 or (r > 0 and r <= float(pl["length_mm"]) / 2.0):
        raise ParamError("base_mesh.plate.curvature_radius_mm must be 0 (flat) or > plate.length_mm / 2")
    return p


def zones(p, band_size):
    """Zone layout for one attempt with weld band element size `band_size` (the value reduced on retries)."""
    pl, w, m = p["plate"], p["web"], p["mesh"]
    tw, half_w = float(w["thickness_mm"]), float(pl["width_mm"]) / 2.0
    bw, ratio, far = float(m["weld_band_width_mm"]), float(m["transition_ratio"]), float(m["far_size_mm"])
    trans = min(band_size * ratio, far)
    # plate: |x| limits (outer edge of each zone) with element size, finest first (TCL1)
    plate_zones = [(tw / 2.0, band_size), (tw / 2.0 + bw, band_size), (tw / 2.0 + 2.0 * bw, trans), (half_w, far)]
    plate_planes = sorted({round(s * x, 9) for x, _ in plate_zones[:-1] if x < half_w - 1e-9 for s in (-1.0, 1.0)})
    # web: y limits; first row >= WEB_FIRST_ROW_MIN_MM, zone heights whole multiples of their element size
    s1 = max(band_size, WEB_FIRST_ROW_MIN_MM)
    s2 = max(min(s1 * ratio, far), s1)
    h1 = math.ceil(bw / s1 - 1e-9) * s1
    h2 = h1 + math.ceil(bw / s2 - 1e-9) * s2
    height = float(w["height_mm"])
    web_zones = [(min(h1, height), s1), (min(h2, height), s2), (height, max(far, s2))]
    web_planes = [round(y, 9) for y in (h1, h2) if y < height - 1e-9]
    return {"plate_zones": plate_zones, "plate_planes": plate_planes, "web_zones": web_zones, "web_planes": web_planes,
            "web_first_row_mm": s1, "transition_size_mm": trans}


def surface_height(p, z):
    """Plate top surface height at z (0 for a flat plate); the arc is lowest at the plate ends."""
    r = float(p["plate"]["curvature_radius_mm"])
    if r <= 0:
        return 0.0
    zc = float(p["plate"]["length_mm"]) / 2.0
    return -(r - math.sqrt(r * r - (z - zc) ** 2))
