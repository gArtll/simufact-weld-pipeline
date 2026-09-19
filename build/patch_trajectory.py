# -*- coding: utf-8 -*-
"""build step, offline (Simufact closed, R7 style): set trajectory_modification fields from case settings.
Copied from P0_A4 patch_fullscript.py. Change: target values come from case.json (per side) instead of being copied
from the reference decoded XML. Fields: calculation, orientation_mode, connect_to_nodes (R4), variation_angle [deg],
variation_offset [mm]. Self-check: outer XML identical, decoded diff lines only in these fields, re-decode equal.
Usage: python patch_trajectory.py <Proc dir> <settings.json> [--dry]
settings.json: {"<robot display name>": {"calculation": ..., "orientation_mode": ..., "connect_to_nodes": true,
                 "variation_angle_deg": 0, "variation_offset_mm": 0}, ...}"""
import difflib
import io
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common.qxml import read_text, replace_blobs, strip_blobs, robot_blobs  # noqa: E402

FIELDS = ("calculation", "orientation_mode", "connect_to_nodes", "variation_angle", "variation_offset")


def elem(tag):
    return re.compile(r"<%s\b[^>]*/>|<%s\b[^>]*>[^<]*</%s>" % (tag, tag, tag))


def target_elements(s):
    ang = float(s["variation_angle_deg"])
    off = float(s["variation_offset_mm"]) / 1000.0
    return {
        "calculation": "<calculation>%s</calculation>" % s["calculation"],
        "orientation_mode": "<orientation_mode>%s</orientation_mode>" % s["orientation_mode"],
        "connect_to_nodes": "<connect_to_nodes>%s</connect_to_nodes>" % ("true" if s["connect_to_nodes"] else "false"),
        # Simufact writes 0 rad as unit 0 and a degree entry as unit 1
        "variation_angle": '<variation_angle dimension="1" unit="0" value="0.0"/>' if ang == 0 else
                           '<variation_angle dimension="1" unit="1" value="%r"/>' % ang,
        "variation_offset": '<variation_offset dimension="5" unit="0" value="%r"/>' % off,
    }


def patch(proc_dir, settings, dry=False, log=print):
    F = os.path.join(proc_dir, "robots_properties.xml")
    if os.path.exists(os.path.join(os.path.dirname(os.path.normpath(proc_dir)), "project.lock")):
        raise RuntimeError("project.lock present (Simufact must be closed)")
    src = read_text(F)

    def fix(x, idx):
        root = ET.fromstring(x.strip().encode("utf-8"))
        rname = root.find("name").get("display_name")
        if rname not in settings:
            raise RuntimeError("no trajectory settings for robot %s" % rname)
        tgt = target_elements(settings[rname])
        y = x
        for t in FIELDS:
            found = elem(t).findall(y)
            if len(found) != 1:
                raise RuntimeError("%s: %s found %d times" % (rname, t, len(found)))
            log("%s %s: %s -> %s" % (rname, t, found[0], tgt[t]))
            y = y.replace(found[0], tgt[t], 1)
        ET.fromstring(y.encode("utf-8"))
        return y

    out, before, after = replace_blobs(src, fix)
    diffs = [[l for l in difflib.unified_diff(a.splitlines(), b.splitlines(), n=0, lineterm="") if l[:1] in "+-" and not l.startswith(("+++", "---"))]
             for a, b in zip(before, after)]
    ok = (len(before) == len(settings) and strip_blobs(src) == strip_blobs(out)
          and all(any(f in l for f in FIELDS) for d in diffs for l in d))
    for d in diffs:
        log("\n".join("   " + l.strip() for l in d))
    log("self-check (outer identical, diff lines only in %s): %s" % (FIELDS, ok))
    if not ok:
        raise RuntimeError("patch self-check failed, nothing written")
    if dry:
        return after
    if not os.path.exists(F + ".bak_before_patch"):
        shutil.copy2(F, F + ".bak_before_patch")
    io.open(F, "w", encoding="utf-8", newline="").write(out)
    if robot_blobs(F) != after:
        raise RuntimeError("re-decode after write differs")
    log("WRITTEN %s" % F)
    return after


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    after = patch(sys.argv[1], json.load(io.open(sys.argv[2], encoding="utf-8")), dry="--dry" in sys.argv)
