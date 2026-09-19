# -*- coding: utf-8 -*-
"""Synthetic public example: 100 x 50 x 6 mm plate with a 6 mm thick, 50 mm high web, two-sided fillet welds.
Writes inputs/ next to this file: base_plate.bdf, base_web.bdf (hexahedra, GRID* + CHEXA, mm), window_w001.csv,
window_w002.csv (window defining points on the bead outer line), fixed_nodes.csv (3-2-1), process_parameters.xml and
HS-SYN-180A22V.xml (synthetic, cases/synthetic_xml.py: not Simufact exports, values made up).
Frame: web normal X (faces x = -3 / +3), weld along Z (0..100 mm), plate thickness in -Y.
Usage: python cases/example_synthetic/make_example.py"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from common.bdf import write_bdf  # noqa: E402
from cases.synthetic_xml import heat_source_xml, process_xml  # noqa: E402

INP = os.path.join(HERE, "inputs")
LEG = 5.4
Z = [10.0 * k for k in range(11)]                       # weld length 100 mm, 10 mm elements
PLATE_X = [-25.0, -15.0, -9.0, -3.0, 3.0, 9.0, 15.0, 25.0]
PLATE_Y = [-6.0, -3.0, 0.0]
WEB_X = [-3.0, 0.0, 3.0]
WEB_Y = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]
WINDOW_Z = (10.0, 50.0, 90.0)
END_TIME_S = 60.0
WELD_STEP_S = 0.1
COOLING_MAX_STEP_S = 5.0
WELD_SPEED_MM_S = 4.0
HS_NAME = "HS-SYN-180A22V"


def block_mesh(xs, ys, zs):
    idx, nodes = {}, []
    for k, z in enumerate(zs):
        for j, y in enumerate(ys):
            for i, x in enumerate(xs):
                idx[(i, j, k)] = len(nodes) + 1
                nodes.append((x, y, z))
    hexas = []
    for k in range(len(zs) - 1):
        for j in range(len(ys) - 1):
            for i in range(len(xs) - 1):
                hexas.append([idx[(i, j, k)], idx[(i + 1, j, k)], idx[(i + 1, j + 1, k)], idx[(i, j + 1, k)],
                              idx[(i, j, k + 1)], idx[(i + 1, j, k + 1)], idx[(i + 1, j + 1, k + 1)], idx[(i, j + 1, k + 1)]])
    return nodes, hexas


def write_window(path, label, x):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# example_synthetic window %s: defining points on the bead outer line\n# Length unit: Millimeter [mm]\n#\n" % label)
        f.write("# Orientation: global orientation;x-coordinate;y-coordinate;z-coordinate\n0;1;0;0\n#\n")
        f.write("# order;activity;x-coordinate;y-coordinate;z-coordinate\n")
        for k, z in enumerate(WINDOW_Z, 1):
            f.write("%d;true;%.6f;%.6f;%.6f\n" % (k, x, LEG / 2, z))


def main():
    os.makedirs(INP, exist_ok=True)
    counts = {}
    for name, (xs, ys) in (("base_plate.bdf", (PLATE_X, PLATE_Y)), ("base_web.bdf", (WEB_X, WEB_Y))):
        nodes, hexas = block_mesh(xs, ys, Z)
        write_bdf(os.path.join(INP, name), nodes, hexas, title="example_synthetic %s" % name[:-4])
        counts[name] = (len(nodes), len(hexas))
    write_window(os.path.join(INP, "window_w001.csv"), "w001", -3.0 - LEG / 2)
    write_window(os.path.join(INP, "window_w002.csv"), "w002", 3.0 + LEG / 2)
    with io.open(os.path.join(INP, "fixed_nodes.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("name;directions;target;x_mm;y_mm;z_mm\n")
        f.write("N1;xyz;plate;-25.0;-6.0;0.0\nN2;xy;plate;25.0;-6.0;0.0\nN3;y;plate;-25.0;-6.0;100.0\n")
    process_xml(os.path.join(INP, "process_parameters.xml"), END_TIME_S, WELD_STEP_S, COOLING_MAX_STEP_S)
    heat_source_xml(os.path.join(INP, HS_NAME + ".xml"), HS_NAME, 180.0, 22.0, WELD_SPEED_MM_S)
    total = sum(e for _, e in counts.values())
    print("plate nodes/elements %d/%d, web %d/%d, base elements %d" % (counts["base_plate.bdf"] + counts["base_web.bdf"] + (total,)))


if __name__ == "__main__":
    main()
