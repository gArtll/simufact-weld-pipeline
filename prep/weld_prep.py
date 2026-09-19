# -*- coding: utf-8 -*-
"""prep step: root line -> bead mesh (hmbatch) -> outer-surface line -> bead checks, one run, one directory (R13).
All paths are arguments; weldsim.py builds them from case.json and config.yaml.
Self-checks (exit 1 on failure): R1 base/bead bdf format, R8 root line on web face, R2 detJ non-positive = 0,
R19 window line length (when --expect-len given), hard rule 8 CSV newline, bead element count = spans*3,
C4c micro-snap: 10 um sub-band = 0 (when --snap-tol > 0)."""
import argparse
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from common.bdf import check_r1, read_bdf  # noqa: E402
from prep.frame import LEGACY_ALONG, LEGACY_NORMAL, check_frame, coord, fmt, is_legacy, parse_vec  # noqa: E402

GATE = os.path.join(ROOT, "gate")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--web", required=True)
    p.add_argument("--plate", required=True)
    p.add_argument("--leg", type=float, required=True)
    p.add_argument("--spacing", type=float, required=True)
    p.add_argument("--side", action="append", required=True, help="label:neg|pos")
    p.add_argument("--window", action="append", required=True, help="label=csv (window defining points, R19)")
    p.add_argument("--out", required=True)
    p.add_argument("--hmbatch", default="", help="HyperMesh hmbatch executable; empty = prep/mesh_bead.py")
    p.add_argument("--snap-tol", type=float, default=0.01)
    p.add_argument("--expect-len", type=float, default=None)
    p.add_argument("--len-tol", type=float, default=0.01)
    p.add_argument("--expected-line", choices=("root", "outer"), default="outer")
    p.add_argument("--points", type=int, default=23)
    p.add_argument("--edge-mode", choices=("planar", "closed_circular"), default="planar")
    p.add_argument("--edge-axis", choices=("x", "y", "z"), default="z")
    p.add_argument("--edge-axis-value", type=float, default=0.0)
    p.add_argument("--edge-center", default="0,0,0")
    p.add_argument("--edge-radius", type=float)
    p.add_argument("--closed", action="store_true")
    p.add_argument("--section-frame", choices=("web_plane", "tube_on_plate"), default="web_plane")
    p.add_argument("--normal", default=fmt(LEGACY_NORMAL), help="web normal of the local joint frame (prep/frame.py)")
    p.add_argument("--along", default=fmt(LEGACY_ALONG), help="weld direction of the local joint frame")
    a = p.parse_args()
    normal, along = parse_vec(a.normal), parse_vec(a.along)
    if a.section_frame == "web_plane":
        check_frame(normal, along)
    legacy = is_legacy(normal, along)
    OUT = a.out
    os.makedirs(OUT, exist_ok=True)
    sides = [s.split(":") for s in a.side]
    windows = dict(w.split("=", 1) for w in a.window)
    S = {"inputs": vars(a), "steps": {}, "checks": {}}

    def save():
        io.open(os.path.join(OUT, "weld_prep_summary.json"), "w", encoding="utf-8").write(json.dumps(S, ensure_ascii=False, indent=1) + "\n")

    def check(name, ok, detail):
        S["checks"][name] = {"ok": bool(ok), "detail": detail}
        save()
        print("CHECK %-28s %s  %s" % (name, "OK" if ok else "FAIL", detail))
        return ok

    def run(name, cmd, cwd=None, env=None):
        log = os.path.join(OUT, name + ".log")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd, env=env)
        io.open(log, "w", encoding="utf-8").write("CMD %s\n--- stdout\n%s\n--- stderr\n%s\nexit=%d\n" % (cmd, r.stdout, r.stderr, r.returncode))
        S["steps"][name] = {"exit": r.returncode, "log": log}
        save()
        if r.returncode:
            print("!! step %s failed (exit %d), log %s" % (name, r.returncode, log))
            sys.exit(1)
        return r

    ok_all = True
    for f in (a.web, a.plate):
        ok, st = check_r1(f)
        ok_all &= check("R1 " + os.path.basename(f), ok, st)
    web_nodes, _, _ = read_bdf(a.web)
    xs = [coord(v, normal) for v in web_nodes.values()]
    face = {"neg": min(xs), "pos": max(xs)}
    S["web_face_x_mm"] = face                  # web face positions along the frame normal (x in the legacy frame)
    S["frame"] = {"normal": list(normal), "along": list(along), "legacy": legacy}

    for lab, side in sides:
        wlcmd = [sys.executable, os.path.join(HERE, "wl_v2.py"), "--web", a.web, "--plate", a.plate, "--face-x", repr(face[side]),
                             "--label", lab, "--window-csv", windows[lab], "--n", str(a.points),
                             "--out-full", os.path.join(OUT, "root_full_%s.csv" % lab), "--out-short", os.path.join(OUT, "wl_root_short_%s.csv" % lab),
                             "--json", os.path.join(OUT, "wl_v2_%s.json" % lab), "--edge-mode", a.edge_mode,
                             "--axis", a.edge_axis, "--axis-value", repr(a.edge_axis_value), "--center", a.edge_center]
        if not legacy:
            wlcmd += ["--normal=" + fmt(normal), "--along=" + fmt(along)]
        if a.edge_radius is not None:
            wlcmd += ["--radius", repr(a.edge_radius)]
        if a.closed:
            wlcmd.append("--closed")
        run("wl_v2_" + lab, wlcmd)
        w = json.load(io.open(os.path.join(OUT, "wl_v2_%s.json" % lab)))
        ok_all &= check("R8 root on face " + lab, w["R8_max_abs_x_minus_face_mm"] < 1e-6, w["R8_max_abs_x_minus_face_mm"])
        if a.expect_len is not None and a.expected_line == "root":
            ok_all &= check("R19 root window length " + lab, abs(w["short_len_mm"] - a.expect_len) < a.len_tol,
                            {"line_len_mm": w["short_len_mm"], "diff_mm": w["short_len_mm"] - a.expect_len})

    params = os.path.join(OUT, "mesh_bead_params.tcl")
    with io.open(params, "w", encoding="ascii", newline="\n") as f:
        f.write("set WEB_BDF {%s}\nset PLATE_BDF {%s}\nset SNAP_TOL %r\nset LEG %r\nset SPACING %r\nset SECTION_FRAME %s\nset EDGE_CENTER {%s}\nset EDGE_AXIS %s\nset CLOSED %d\nset BEADS {\n" % (
            a.web.replace("\\", "/"), a.plate.replace("\\", "/"), a.snap_tol, a.leg, a.spacing, a.section_frame, a.edge_center.replace(",", " "), a.edge_axis, int(a.closed)))
        for lab, side in sides:
            f.write("    {%s %s %r {%s} {%s}}\n" % (lab, side, face[side], os.path.join(OUT, "root_full_%s.csv" % lab).replace("\\", "/"),
                                                     os.path.join(OUT, "bead_%s.bdf" % lab).replace("\\", "/")))
        f.write("}\n")
    if a.hmbatch and not legacy:
        # the Tcl mesher knows only the legacy frame; the Python one is the same algorithm in any frame
        S["hmbatch_skipped"] = "local frame is not the legacy +x/+z frame: prep/mesh_bead.py used"
        print("hmbatch skipped: %s" % S["hmbatch_skipped"])
    if a.hmbatch and legacy:
        mesher = "hmbatch_mesh_bead"
        env = dict(os.environ, WELD_PREP_PARAMS=params.replace("\\", "/"))
        run(mesher, [a.hmbatch, "-tcl", os.path.join(HERE, "mesh_bead.tcl")], cwd=OUT, env=env)
    else:
        mesher = "mesh_bead"
        pjson = os.path.join(OUT, "mesh_bead_params.json")
        mb = {
            "web_bdf": a.web, "plate_bdf": a.plate, "snap_tol": a.snap_tol, "leg": a.leg, "spacing": a.spacing,
            "section_frame": a.section_frame, "edge_center": [float(v) for v in a.edge_center.split(",")], "edge_axis": a.edge_axis,
            "closed": a.closed, "beads": [{"label": lab, "side": side, "face_x": face[side], "root_full_csv": os.path.join(OUT, "root_full_%s.csv" % lab),
                                           "out_bdf": os.path.join(OUT, "bead_%s.bdf" % lab)} for lab, side in sides]}
        if not legacy:
            mb["frame"] = {"normal": list(normal), "along": list(along)}
        io.open(pjson, "w", encoding="utf-8").write(json.dumps(mb, indent=1) + "\n")
        run(mesher, [sys.executable, os.path.join(HERE, "mesh_bead.py"), pjson], cwd=OUT)
    S["mesher"] = mesher
    beads = {lab: os.path.join(OUT, "bead_%s.bdf" % lab) for lab, _ in sides}
    hm = io.open(os.path.join(OUT, mesher + ".log"), encoding="utf-8").read()
    S["snap_log"] = [l for l in hm.splitlines() if l.startswith(("SNAP", "BEAD "))]
    for lab, _ in sides:
        if not os.path.exists(beads[lab]):
            check("bead written " + lab, False, beads[lab])
            sys.exit(1)
        ok, st = check_r1(beads[lab])
        ok_all &= check("R1 bead " + lab, ok and st["cpenta"] == 0, st)
        nodes, elems, _ = read_bdf(beads[lab])
        expected = 3 * (len(nodes) // 7 if a.closed else len(nodes) // 7 - 1)
        ok_all &= check("bead elements = spans*3 " + lab, len(elems) == expected, {"elements": len(elems), "sections": len(nodes) // 7, "closed": a.closed})

    for lab, _ in sides:
        cmd = [sys.executable, os.path.join(HERE, "bead_outer_line.py"), "--bead", beads[lab], "--out", os.path.join(OUT, "wl_outer_%s.csv" % lab),
               "--clip-csv", windows[lab], "--header-csv", windows[lab], "--n", str(a.points),
               "--json", os.path.join(OUT, "bead_outer_line_%s.json" % lab)]
        if a.expect_len is not None and a.expected_line == "outer":
            cmd += ["--expect-len", repr(a.expect_len), "--len-tol", repr(a.len_tol)]
        if a.closed:
            cmd.append("--closed")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        io.open(os.path.join(OUT, "bead_outer_line_%s.log" % lab), "w", encoding="utf-8").write(r.stdout + r.stderr)
        ol = json.load(io.open(os.path.join(OUT, "bead_outer_line_%s.json" % lab)))
        ok_all &= check("hard rule 8 newline " + lab, ol["ends_with_newline"], os.path.basename(ol["csv"]))
        ok_all &= check("R19 window length " + lab, r.returncode == 0,
                        {"line_len_mm": ol["line_len_mm"], "diff_mm": ol.get("R19_len_diff_mm"), "clip_dist_mm": [ol["clip_start_dist_mm"], ol["clip_end_dist_mm"]]})

    cmd = [sys.executable, os.path.join(GATE, "check_bead_nodes.py"), "--web", a.web, "--plate", a.plate, "--out-dir", os.path.join(OUT, "bead_nodes")]
    for lab, _ in sides:
        cmd += ["--bead", beads[lab], "--trajectory", os.path.join(OUT, "wl_root_short_%s.csv" % lab)]
    run("check_bead_nodes", cmd)
    run("check_detj", [sys.executable, os.path.join(GATE, "check_detj.py")] + list(beads.values()) + ["--json", os.path.join(OUT, "detj.json")])
    dj = json.load(io.open(os.path.join(OUT, "detj.json")))
    for k, v in dj.items():
        ok_all &= check("R2 detJ " + os.path.basename(k), v["nonpositive"] == 0, {"min_detJ": v["min_detJ"], "nonpositive": v["nonpositive"]})
    import csv as _csv
    sub = {}
    for row in _csv.DictReader(io.open(os.path.join(OUT, "bead_nodes", "bead_node_histogram.csv"), encoding="utf-8-sig")):
        if row["bin_mm"] in ("[1e-06,0.0001)", "[0.0001,0.001)", "[0.001,0.01)"):
            sub[row["bead"]] = sub.get(row["bead"], 0) + int(row["count"])
    S["subband_10um"] = sub
    if a.snap_tol > 0:
        for b in beads.values():
            ok_all &= check("micro-snap 10um sub-band " + os.path.basename(b), sub.get(os.path.basename(b), 0) == 0, sub.get(os.path.basename(b), 0))
    S["beads"] = beads
    S["outer_lines"] = {lab: os.path.join(OUT, "wl_outer_%s.csv" % lab) for lab, _ in sides}
    S["phase"] = "done" if ok_all else "checks_failed"
    save()
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
