# -*- coding: utf-8 -*-
"""Synthetic public example of the tube-on-plate joint type: a 5 mm wall tube (outer radius 42.5 mm, 60 mm high)
standing on a 200 x 200 x 10 mm plate, one closed circumferential fillet weld.

Every file is generated here from the constants below; nothing comes from a project. Writes inputs/ next to this file:
  base_plate.bdf   structured hexahedra, 5 mm cells, 3 layers, plate top at z = 0
  base_tube.bdf    hexahedra in cylindrical layout: 48 around, 2 through the wall, 5 mm high rows
  window_ring.csv  weld window: the whole outer ring, start = end
  fixed_nodes.csv  3-2-1 support of the plate bottom corners
  process_parameters.xml, HS-SYN-200A24V.xml   synthetic (cases/synthetic_xml.py; not Simufact exports)
Usage: python cases/example_tube_synthetic/make_example.py"""
import io
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from common.bdf import write_bdf  # noqa: E402
from cases.synthetic_xml import heat_source_xml, process_xml  # noqa: E402

INP = os.path.join(HERE, "inputs")
CENTER = (100.0, 100.0)
R_IN, R_OUT, HEIGHT = 37.5, 42.5, 60.0
N_AROUND, N_WALL, ROW_MM = 48, 2, 5.0
PLATE, PLATE_T, CELL, PLATE_LAYERS = 200.0, 10.0, 5.0, 3
END_TIME_S, WELD_STEP_S, COOLING_MAX_STEP_S, WELD_SPEED_MM_S = 120.0, 0.1, 5.0, 4.0
HS_NAME = "HS-SYN-200A24V"


def plate():
    n = int(round(PLATE / CELL))
    xs = [CELL * i for i in range(n + 1)]
    zs = [-PLATE_T + PLATE_T * k / PLATE_LAYERS for k in range(PLATE_LAYERS + 1)]
    idx, nodes = {}, []
    for k, z in enumerate(zs):
        for j, y in enumerate(xs):
            for i, x in enumerate(xs):
                idx[(i, j, k)] = len(nodes) + 1
                nodes.append((x, y, z))
    hexas = [[idx[(i, j, k)], idx[(i + 1, j, k)], idx[(i + 1, j + 1, k)], idx[(i, j + 1, k)],
              idx[(i, j, k + 1)], idx[(i + 1, j, k + 1)], idx[(i + 1, j + 1, k + 1)], idx[(i, j + 1, k + 1)]]
             for k in range(PLATE_LAYERS) for j in range(n) for i in range(n)]
    return nodes, hexas


def tube():
    rs = [R_IN + (R_OUT - R_IN) * k / N_WALL for k in range(N_WALL + 1)]
    zs = [ROW_MM * k for k in range(int(round(HEIGHT / ROW_MM)) + 1)]
    idx, nodes = {}, []
    for h, z in enumerate(zs):
        for a in range(N_AROUND):
            t = 2.0 * math.pi * a / N_AROUND
            for r_i, r in enumerate(rs):
                idx[(a, r_i, h)] = len(nodes) + 1
                nodes.append((CENTER[0] + r * math.cos(t), CENTER[1] + r * math.sin(t), z))
    hexas = []
    for h in range(len(zs) - 1):
        for a in range(N_AROUND):
            b = (a + 1) % N_AROUND
            for r_i in range(N_WALL):
                hexas.append([idx[(a, r_i, h)], idx[(a, r_i + 1, h)], idx[(b, r_i + 1, h)], idx[(b, r_i, h)],
                              idx[(a, r_i, h + 1)], idx[(a, r_i + 1, h + 1)], idx[(b, r_i + 1, h + 1)], idx[(b, r_i, h + 1)]])
    return nodes, hexas


def window(path):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# example_tube_synthetic weld window: the whole outer ring at the plate top, start = end\n"
                "# Length unit: Millimeter [mm]\n#\n"
                "# Orientation: global orientation;x-coordinate;y-coordinate;z-coordinate\n0;1;0;0\n#\n"
                "# order;activity;x-coordinate;y-coordinate;z-coordinate\n")
        for k in range(N_AROUND + 1):
            t = 2.0 * math.pi * k / N_AROUND
            f.write("%d;true;%.7f;%.7f;%.7f\n" % (k + 1, CENTER[0] + R_OUT * math.cos(t), CENTER[1] + R_OUT * math.sin(t), 0.0))


def main():
    os.makedirs(INP, exist_ok=True)
    for name, (nodes, hexas) in (("base_plate.bdf", plate()), ("base_tube.bdf", tube())):
        write_bdf(os.path.join(INP, name), nodes, hexas, title="example_tube_synthetic %s" % name[:-4])
        print("%s: %d nodes, %d CHEXA" % (name, len(nodes), len(hexas)))
    window(os.path.join(INP, "window_ring.csv"))
    with io.open(os.path.join(INP, "fixed_nodes.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("name;directions;target;x_mm;y_mm;z_mm\n")
        f.write("N1;xyz;plate;0.0;0.0;-10.0\nN2;yz;plate;200.0;0.0;-10.0\nN3;z;plate;0.0;200.0;-10.0\n")
    process_xml(os.path.join(INP, "process_parameters.xml"), END_TIME_S, WELD_STEP_S, COOLING_MAX_STEP_S)
    heat_source_xml(os.path.join(INP, HS_NAME + ".xml"), HS_NAME, 200.0, 24.0, WELD_SPEED_MM_S)
    ring = 2.0 * N_AROUND * R_OUT * math.sin(math.pi / N_AROUND)
    print("weld ring through %d nodes: %.10f mm" % (N_AROUND, ring))


if __name__ == "__main__":
    main()
