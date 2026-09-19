# -*- coding: utf-8 -*-
"""mesh_transition: the reference T2_pair and T3 templates, their tilings, and the checks that guard them.

Nothing here needs HyperMesh or Simufact. The metrics are this project's own (mesh_transition/quality.py); none of
them is asserted to equal a HyperMesh metric."""
import io
import math
import os
import tempfile
import unittest

from mesh_transition import check_all, quality_table, strip, t2_pair, t3
from mesh_transition import block, corner, face, nastran, quality, solid, step
from mesh_transition.mesh import Mesh, boundary_edges, flat_corners, hanging_nodes
from mesh_transition.quality import corner_values, metrics, summary, worst_element
from mesh_transition.svg import draw
from mesh_transition.templates import can_build


class TestMetricConvention(unittest.TestCase):
    def test_unit_square_is_the_reference_point(self):
        m = metrics([(0, 0), (1, 0), (1, 1), (0, 1)])
        self.assertAlmostEqual(m["corner_jacobian"], 1.0)
        self.assertAlmostEqual(m["skew_deg"], 0.0)
        self.assertAlmostEqual(m["aspect_ratio"], 1.0)
        self.assertAlmostEqual(m["area"], 1.0)
        self.assertEqual([round(a, 6) for _, a in corner_values([(0, 0), (1, 0), (1, 1), (0, 1)])], [90.0] * 4)

    def test_trapezoid_angles_by_hand(self):
        """(0,0) (3,0) (2,1) (1,1): the sides rise at 45 degrees, so the angles are 45 / 45 / 135 / 135."""
        p = [(0, 0), (3, 0), (2, 1), (1, 1)]
        self.assertEqual(sorted(round(a, 6) for _, a in corner_values(p)), [45.0, 45.0, 135.0, 135.0])
        self.assertAlmostEqual(metrics(p)["corner_jacobian"], math.sin(math.radians(45)))
        self.assertAlmostEqual(metrics(p)["skew_deg"], 45.0)

    def test_a_corner_past_180_degrees_is_negative(self):
        p = [(0, 0), (2, 0), (1, 0.4), (0, 1)]                 # third corner folded past the straight line
        self.assertLess(metrics(p)["corner_jacobian"], 0.0)


class TestTemplates(unittest.TestCase):
    def test_t3_is_four_quads_over_two_interior_nodes(self):
        m = t3()
        c = check_all(m)
        self.assertEqual((c["elements"], c["interior_nodes"], c["boundary_edges"]), (4, 2, 6))
        self.assertTrue(c["ok"], c)

    def test_t2_pair_is_six_quads_over_three_interior_nodes(self):
        m = t2_pair()
        c = check_all(m)
        self.assertEqual((c["elements"], c["interior_nodes"], c["boundary_edges"]), (6, 3, 8))
        self.assertTrue(c["ok"], c)

    def test_euler_relation_holds_for_both(self):
        for m in (t3(), t2_pair()):
            c = check_all(m)
            self.assertEqual(c["elements"], c["boundary_edges"] // 2 + c["interior_nodes"] - 1)

    def test_no_flat_corner_and_no_hanging_node(self):
        for m in (t3(), t2_pair(), strip([2, 2, 3])[0]):
            self.assertEqual(flat_corners(m), [])
            self.assertEqual(hanging_nodes(m), [])

    def test_interior_nodes_carry_at_least_three_elements(self):
        for m in (t3(), t2_pair()):
            self.assertGreaterEqual(check_all(m)["interior_node_min_elements"], 3)


class TestClosure(unittest.TestCase):
    def test_one_2to1_cell_can_never_be_all_quad(self):
        ok, why = can_build([2])
        self.assertFalse(ok)
        self.assertIn("odd", why)

    def test_a_2to1_cell_cannot_pair_with_a_3to1(self):
        ok, why = can_build([2, 3])
        self.assertFalse(ok)
        self.assertIn("odd", why)

    def test_scattered_2to1_passes_parity_but_not_the_templates(self):
        """[2,3,2] has an even boundary count and an even n2, yet the reference templates cannot cover it."""
        cells = [2, 3, 2]
        self.assertEqual((len(cells) + sum(cells) + 2) % 2, 0)
        self.assertEqual(cells.count(2) % 2, 0)
        ok, why = can_build(cells)
        self.assertFalse(ok)
        self.assertIn("adjacent", why)

    def test_sequences_that_do_close(self):
        for cells, want in (([3], ["T3"]), ([2, 2], ["T2_pair"]), ([2, 2, 3], ["T2_pair", "T3"]),
                            ([3, 2, 2, 3], ["T3", "T2_pair", "T3"])):
            ok, res = can_build(cells)
            self.assertTrue(ok, cells)
            self.assertEqual(res, want)


class TestStrips(unittest.TestCase):
    def test_tiling_welds_and_stays_clean(self):
        for cells, n_el in (([3, 3, 3], 12), ([2, 2, 2, 2, 2, 2], 18), ([2, 2, 3, 3], 14)):
            m, zones = strip(cells)
            c = check_all(m)
            self.assertTrue(c["ok"], (cells, c))
            self.assertEqual(c["elements"], n_el)
            self.assertEqual(len(zones), n_el)

    def test_tiles_share_nodes_instead_of_duplicating_them(self):
        """Two T3 side by side: 8 + 8 nodes, the two on the shared vertical edge welded -> 14, not 16."""
        self.assertEqual(len(strip([3, 3])[0].nodes), 14)
        self.assertEqual(len(t3().nodes), 8)

    def test_mixed_strip_keeps_the_fine_and_coarse_counts(self):
        cells = [2, 2, 3, 3]
        m, _ = strip(cells, h=1.0)
        xs_top = sorted({x for x, y in m.nodes.values() if abs(y - 2.0) < 1e-9})
        xs_bot = sorted({x for x, y in m.nodes.values() if abs(y) < 1e-9})
        self.assertEqual(len(xs_top) - 1, sum(cells))          # 10 fine edges
        self.assertEqual(len(xs_bot) - 1, len(cells))          # 4 coarse edges

    def test_refuses_a_sequence_it_cannot_build(self):
        with self.assertRaises(ValueError):
            strip([2, 3, 2])


class TestChecksCatchBadMeshes(unittest.TestCase):
    """The checks have to fail on meshes that are wrong, or they are worth nothing."""

    def test_collapsed_quad_is_caught(self):
        m = Mesh()
        a, b, c = m.node(0, 0), m.node(1, 0), m.node(1, 1)
        m.quads[1] = (a, b, c, c)
        self.assertEqual(check_all(m)["duplicate_node_elements"], [1])

    def test_hanging_node_is_caught(self):
        """A fine node resting on a coarse element's edge: conformal-looking connectivity, non-conformal mesh."""
        m = Mesh()
        m.quad(m.node(0, 0), m.node(2, 0), m.node(2, 1), m.node(0, 1))
        m.quad(m.node(0, 1), m.node(1, 1), m.node(1, 2), m.node(0, 2))
        m.quad(m.node(1, 1), m.node(2, 1), m.node(2, 2), m.node(1, 2))
        h = hanging_nodes(m)
        self.assertEqual([x["node"] for x in h], [m.node(1, 1)])

    def test_flat_corner_is_caught(self):
        m = Mesh()
        m.quad(m.node(0, 0), m.node(1, 0), m.node(2, 0), m.node(1, 1))
        self.assertTrue(flat_corners(m))

    def test_worst_element_is_the_one_with_the_lowest_metric(self):
        m, _ = strip([3])
        w = worst_element(m)
        self.assertEqual(metrics(m.corners(w))["corner_jacobian"], summary(m)["min_corner_jacobian"])


class TestWelding(unittest.TestCase):
    """Two bugs found while mapping a template row onto a 3.8 m weld root; both left a mesh that every local check
    passed and only the Euler relation caught."""

    def test_points_straddling_a_bucket_edge_still_weld(self):
        """Quantising a coordinate into one bucket is not a tolerance."""
        from mesh_transition.mesh import WELD_TOL
        m = Mesh()
        x = 2312.677250535418
        a = m.node(x, 10.0)
        b = m.node(x + WELD_TOL / 10.0, 10.0)                  # far closer than the tolerance, other side of a bucket
        self.assertEqual(a, b)
        self.assertEqual(len(m.nodes), 1)

    def test_a_long_strip_welds_to_rows_summed_in_a_different_order(self):
        """The strip accumulates per template, a row of plain quads per cell; over thousands of millimetres the two
        sums differ in the last bits, and a tolerance that cannot absorb that leaves a slit."""
        h, n = 5.0057894736842105, 300
        cells = [2] * n
        m, zones = strip(cells, h=h, height=2 * h)
        for k in range(2 * n):                                  # fine row welded onto the strip's fine side
            m.quad(m.node(k * h, 2 * h), m.node((k + 1) * h, 2 * h),
                   m.node((k + 1) * h, 3 * h), m.node(k * h, 3 * h))
        x, xs = 0.0, [0.0]
        for c in cells:
            x += c * h
            xs.append(x)
        for k in range(len(xs) - 1):                            # coarse row welded onto the coarse side
            m.quad(m.node(xs[k], -h * 2), m.node(xs[k + 1], -h * 2), m.node(xs[k + 1], 0.0), m.node(xs[k], 0.0))
        c = check_all(m)
        self.assertTrue(c["euler_ok"], (c["elements"], c["euler_elements_expected"]))
        self.assertTrue(c["ok"], {k: v for k, v in c.items() if isinstance(v, list) and v})

    def test_hanging_node_check_is_not_quadratic(self):
        """It is bucketed; a few thousand elements must still be a fraction of a second."""
        import time
        m, _ = strip([2, 2] * 400)
        t0 = time.time()
        hanging_nodes(m)
        self.assertLess(time.time() - t0, 5.0)


class TestOutputs(unittest.TestCase):
    def test_svg_carries_node_and_element_numbers_and_marks_the_worst(self):
        m, zones = strip([2, 2])
        with tempfile.TemporaryDirectory() as d:
            p = draw(m, os.path.join(d, "x.svg"), title="t", zones=zones)
            text = open(p, encoding="utf-8").read()
        self.assertTrue(text.startswith("<svg"))
        self.assertIn(">E1<", text)
        self.assertIn(">11<", text)
        self.assertIn("worst E", text)
        self.assertEqual(text.count("<polygon"), len(m.quads))

    def test_quality_table_has_one_row_per_element(self):
        m, zones = strip([3, 3])
        rows = quality_table(m, zones).splitlines()
        self.assertEqual(len(rows) - 2, len(m.quads))

class TestFaceExtraction(unittest.TestCase):
    """Reading a 2D face back out of a swept 3D mesh, so an existing mesh can be compared with an explicit one."""

    BDF = "\n".join([
        "$ two hexahedra and one wedge, swept 1 mm along x",
        "GRID           1       0     0.0     0.0     0.0",
        "GRID           2       0     0.0     1.0     0.0",
        "GRID           3       0     0.0     1.0     1.0",
        "GRID           4       0     0.0     0.0     1.0",
        "GRID           5       0     0.0     2.0     0.0",
        "GRID           6       0     0.0     2.0     1.0",
        "GRID          11       0     1.0     0.0     0.0",
        "GRID          12       0     1.0     1.0     0.0",
        "GRID          13       0     1.0     1.0     1.0",
        "GRID          14       0     1.0     0.0     1.0",
        "GRID          15       0     1.0     2.0     0.0",
        "GRID          16       0     1.0     2.0     1.0",
        "CHEXA          1       1       1       2       3       4      11      12+",
        "+             13      14",
        "CPENTA         2       1       2       5       6      12      15      16",
        "ENDDATA",
        ""])

    def write(self, d):
        p = os.path.join(d, "m.bdf")
        io.open(p, "w", encoding="latin-1", newline="\n").write(self.BDF)
        return p

    def test_reads_grid_chexa_and_cpenta(self):
        with tempfile.TemporaryDirectory() as d:
            nodes, elems = face.read_solid_bdf(self.write(d))
        self.assertEqual(len(nodes), 12)
        self.assertEqual([k for k, _ in elems], ["hex8", "penta6"])
        self.assertEqual(nodes[3], (0.0, 1.0, 1.0))
        self.assertEqual(len(elems[0][1]), 8)
        self.assertEqual(len(elems[1][1]), 6)

    def test_face_gives_four_nodes_for_a_hex_and_three_for_a_wedge(self):
        """This is what turns a swept mesh back into the 2D problem: a wedge was a triangle."""
        with tempfile.TemporaryDirectory() as d:
            nodes, elems = face.read_solid_bdf(self.write(d))
        on, cells = face.face_cells(nodes, elems, "x", 0.0)
        self.assertEqual(len(on), 6)
        self.assertEqual(sorted(len(f) for _, f in cells), [3, 4])
        self.assertEqual(face.levels(nodes, "x"), [(0.0, 6), (1.0, 6)])

    def test_small_field_exponent_notation(self):
        self.assertAlmostEqual(face._nf("1.23+4"), 12300.0)
        self.assertAlmostEqual(face._nf("-5.0-2"), -0.05)
        self.assertAlmostEqual(face._nf(" 2.5 "), 2.5)
        self.assertEqual(face._nf("  "), 0.0)


class TestResample(unittest.TestCase):
    def test_uniform_spacing_and_endpoints_kept(self):
        poly = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]          # one long leg and a corner
        out = face.resample(poly, 1.0)
        self.assertEqual(out[0], poly[0])
        self.assertAlmostEqual(out[-1][0], 10.0)
        self.assertAlmostEqual(out[-1][1], 10.0)
        seg = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(out, out[1:])]
        self.assertAlmostEqual(max(seg), min(seg), places=9)
        self.assertAlmostEqual(sum(seg), 20.0, places=9)

    def test_a_single_long_segment_is_broken_up(self):
        """A boundary read off a mesh carries that mesh's node spacing, odd long segments included."""
        poly = [(0.0, 0.0), (2.0, 0.0), (35.0, 0.0)]
        out = face.resample(poly, 2.0)
        seg = [b[0] - a[0] for a, b in zip(out, out[1:])]
        self.assertLess(max(seg), 2.1)


class TestSweep(unittest.TestCase):
    """2D all-QUAD has to give 3D all-HEX. The volumes and the metric are pinned to shapes worked out by hand."""

    def square(self):
        m = Mesh()
        m.quad(m.node(0, 0), m.node(1, 0), m.node(1, 1), m.node(0, 1))
        return m

    def test_a_unit_square_swept_one_millimetre_is_a_unit_cube(self):
        s, info = solid.sweep(self.square(), ("x", "y"), "z", 0.0, 1.0, 1)
        kind, ids = s.elements[0]
        self.assertEqual(kind, "hex8")
        self.assertAlmostEqual(solid.volume(s.xyz(ids)), 1.0, places=9)
        self.assertAlmostEqual(solid.corner_scaled_jacobian(s.xyz(ids), solid.HEX_CORNERS), 1.0, places=9)
        self.assertFalse(info["flipped_for_positive_volume"])

    def test_volume_of_a_box(self):
        m = Mesh()
        m.quad(m.node(0, 0), m.node(2, 0), m.node(2, 3), m.node(0, 3))
        s, _ = solid.sweep(m, ("x", "y"), "z", 0.0, 4.0, 1)
        self.assertAlmostEqual(solid.volume(s.xyz(s.elements[0][1])), 24.0, places=9)

    def test_a_triangle_sweeps_to_a_wedge_of_half_the_volume(self):
        """This is why the 2D topology decides the wedge count: a triangle can only become a PENTA."""
        m = Mesh()
        c = (m.node(0, 0), m.node(1, 0), m.node(0, 1))
        s, _ = solid.sweep(m, ("x", "y"), "z", 0.0, 1.0, 1, cells=[c])
        self.assertEqual(s.elements[0][0], "penta6")
        self.assertAlmostEqual(solid.volume(s.xyz(s.elements[0][1])), 0.5, places=9)

    def test_clockwise_input_is_flipped_rather_than_left_inside_out(self):
        m = Mesh()
        m.quad(m.node(0, 0), m.node(0, 1), m.node(1, 1), m.node(1, 0))
        s, info = solid.sweep(m, ("x", "y"), "z", 0.0, 1.0, 2)
        self.assertTrue(info["flipped_for_positive_volume"])
        self.assertGreater(solid.check_solid(s)["min_volume"], 0.0)

    def test_an_all_quad_row_sweeps_to_all_hex_and_passes_every_3d_check(self):
        m, _ = strip([2, 2, 3, 3])
        self.assertTrue(check_all(m)["ok"])
        s, info = solid.sweep(m, ("x", "y"), "z", 0.0, 6.0, 2)
        c = solid.check_solid(s, info["layers"], info["nodes_per_layer"])
        self.assertEqual(c["penta6"], 0)
        self.assertEqual(c["hex8"], c["elements"])
        self.assertEqual(c["elements"], 2 * len(m.quads))
        for k in ("duplicate_node_elements", "zero_volume_elements", "negative_volume_elements",
                  "non_positive_jacobian_elements"):
            self.assertEqual(c[k], [], k)
        self.assertEqual(c["faces_shared_by_more_than_two_count"], 0)
        self.assertTrue(c["layer_nodes_ok"])
        self.assertTrue(c["ok"])

    def test_layers_line_up(self):
        m, _ = strip([3, 3])
        s, info = solid.sweep(m, ("x", "y"), "z", 0.0, 6.0, 3)
        self.assertEqual(len(s.nodes), len(m.nodes) * 4)
        zs = sorted({round(p[2], 9) for p in s.nodes.values()})
        self.assertEqual(zs, [0.0, 2.0, 4.0, 6.0])
        for z in zs:                                         # every layer carries the same in-plane pattern
            self.assertEqual(len([1 for p in s.nodes.values() if abs(p[2] - z) < 1e-9]), len(m.nodes))

    def test_a_collapsed_hexahedron_is_caught(self):
        """A CHEXA naming the same node twice: a wedge wearing a hexahedron card, which is what a solver DAT gives
        back and what counting CPENTA cards cannot see."""
        s = solid.Solid()
        n = [s.add_node(*p) for p in [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1)]]
        s.elements.append(("hex8", [n[0], n[1], n[2], n[2], n[3], n[4], n[5], n[5]]))
        c = solid.check_solid(s)
        self.assertEqual(c["duplicate_node_elements"], [1])
        self.assertFalse(c["ok"])


STEP_TEXT = "\n".join([
    "ISO-10303-21;",
    "HEADER;",
    "FILE_DESCRIPTION (( 'STEP AP203' ), '1' );",
    "ENDSEC;",
    "",
    "DATA;",
    "#1 = CARTESIAN_POINT ( 'NONE', ( 0.0, 0.0, 0.0 ) ) ;",
    "#2 = CARTESIAN_POINT ( 'NONE', ( 10.0, 0.0, 0.0 ) ) ;",
    "#3 = CARTESIAN_POINT ( 'NONE', ( 10.0, 10.0, 0.0 ) ) ;",
    "#4 = CARTESIAN_POINT ( 'NONE', ( 0.0, 10.0, 0.0 ) ) ;",
    "#10 = DIRECTION ( 'NONE', ( 1.0, 0.0, 0.0 ) ) ;",
    "#11 = DIRECTION ( 'NONE', ( 0.0, 0.0, 1.0 ) ) ;",
    "#12 = VECTOR ( 'NONE', #10, 1000.0 ) ;",
    "#20 = LINE ( 'NONE', #1, #12 ) ;",
    "#30 = AXIS2_PLACEMENT_3D ( 'NONE', #1, #11, #10 ) ;",
    "#31 = CIRCLE ( 'NONE', #30, 5.0 ) ;",
    "#40 = B_SPLINE_CURVE_WITH_KNOTS ( 'NONE', 1, ( #1, #2, #3 ),",
    "  .UNSPECIFIED., .F., .F., ( 2, 1, 2 ), ( 0.0, 0.5, 1.0 ), .UNSPECIFIED. ) ;",
    "#50 = VERTEX_POINT ( 'NONE', #1 ) ;",
    "#51 = VERTEX_POINT ( 'NONE', #2 ) ;",
    "#52 = VERTEX_POINT ( 'NONE', #3 ) ;",
    "#60 = EDGE_CURVE ( 'NONE', #50, #51, #20, .T. ) ;",
    "#61 = EDGE_CURVE ( 'NONE', #50, #52, #40, .T. ) ;",
    "#70 = ORIENTED_EDGE ( 'NONE', *, *, #60, .T. ) ;",
    "#71 = ORIENTED_EDGE ( 'NONE', *, *, #61, .T. ) ;",
    "#80 = EDGE_LOOP ( 'NONE', ( #70, #71 ) ) ;",
    "#81 = FACE_OUTER_BOUND ( 'NONE', #80, .T. ) ;",
    "#90 = PLANE ( 'NONE', #30 ) ;",
    "#91 = ADVANCED_FACE ( 'NONE', ( #81 ), #90, .T. ) ;",
    "ENDSEC;",
    "END-ISO-10303-21;",
    ""])


class TestStepReader(unittest.TestCase):
    """Reading a boundary curve from CAD rather than from a mesh. A tiny hand-written file, so no CAD is needed."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.d.name, "t.step")
        io.open(self.p, "w", encoding="latin-1", newline="\n").write(STEP_TEXT)
        self.ents = step.parse(self.p)

    def tearDown(self):
        self.d.cleanup()

    def test_nested_lists_survive_parsing(self):
        """The argument list has brackets inside brackets; a naive split loses the control points."""
        self.assertEqual(self.ents[1][0], "CARTESIAN_POINT")
        self.assertEqual(step.point(self.ents, 2), (10.0, 0.0, 0.0))
        kind, args = self.ents[40]
        self.assertEqual(kind, "B_SPLINE_CURVE_WITH_KNOTS")
        self.assertEqual(args[2], [1, 2, 3])                   # the control point references
        self.assertEqual(args[6], [2.0, 1.0, 2.0])             # multiplicities
        self.assertEqual(args[7], [0.0, 0.5, 1.0])             # knots

    def test_line_uses_the_vector_magnitude(self):
        kind, f, info = step.curve(self.ents, 20)
        self.assertEqual(kind, "LINE")
        self.assertEqual(info["magnitude"], 1000.0)
        self.assertEqual(f(0.0), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(f(0.001)[0], 1.0)

    def test_circle_is_sampled_on_its_own_axes(self):
        kind, f, info = step.curve(self.ents, 31)
        self.assertEqual(info["radius"], 5.0)
        self.assertAlmostEqual(f(0.0)[0], 5.0)
        self.assertAlmostEqual(f(0.25)[1], 5.0)
        self.assertAlmostEqual(f(0.5)[0], -5.0)

    def test_knot_vector_expansion(self):
        self.assertEqual(step.knot_vector([0.0, 0.5, 1.0], [2, 1, 2]), [0.0, 0.0, 0.5, 1.0, 1.0])

    def test_degree_one_bspline_is_the_control_polygon(self):
        _, f, info = step.curve(self.ents, 40)
        self.assertEqual(info["degree"], 1)
        self.assertEqual(f(0.0), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(f(0.5)[0], 10.0)                # the middle control point
        self.assertAlmostEqual(f(0.5)[1], 0.0)
        self.assertAlmostEqual(f(1.0)[0], 10.0)
        self.assertAlmostEqual(f(1.0)[1], 10.0)

    def test_face_edges_walk_the_loop(self):
        es = step.face_edges(self.ents, 91)
        self.assertEqual(len(es), 2)
        self.assertEqual({e["kind"] for e in es}, {"LINE", "B_SPLINE_CURVE_WITH_KNOTS"})
        self.assertEqual(step.faces(self.ents)[0]["surface_kind"], "PLANE")

    def test_an_edge_is_clipped_to_its_vertices(self):
        """A STEP curve is unbounded in parameter: the LINE entity runs 1000 mm, the edge only 10."""
        e = [x for x in step.face_edges(self.ents, 91) if x["kind"] == "LINE"][0]
        pts = step.sample_edge(self.ents, e, 10)
        self.assertEqual(pts[0], (0.0, 0.0, 0.0))
        self.assertAlmostEqual(pts[-1][0], 10.0)
        self.assertAlmostEqual(max(p[0] for p in pts), 10.0)

    def test_a_half_turn_arc_is_refused_rather_than_guessed(self):
        e = {"geometry": 31, "p1": (5.0, 0.0, 0.0), "p2": (-5.0, 0.0, 0.0), "kind": "CIRCLE"}
        with self.assertRaises(ValueError):
            step.sample_edge(self.ents, e, 8)

    def test_chain_edges_orders_by_shared_end_points(self):
        es = step.face_edges(self.ents, 91)
        chains = step.chain_edges(es)
        self.assertEqual(len(chains), 1)
        self.assertEqual(len(chains[0]), 2)


if __name__ == "__main__":
    unittest.main()


class TestCorner(unittest.TestCase):
    """A band that has to follow a boundary through a tangent break."""

    ROWS = [(5.0, 5.0, "buffer1"), (5.0, 5.0, "buffer2"), (10.0, 13.0, "transition"),
            (13.0, 13.0, "coarse1"), (13.0, 13.0, "coarse2")]

    @staticmethod
    def vee(turn_deg, arm=300.0, step=5.0):
        n = int(round(arm / step))
        a = math.radians(turn_deg)
        return ([(-(n - k) * step, 0.0) for k in range(n)]
                + [(math.cos(a) * k * step, math.sin(a) * k * step) for k in range(n + 1)])

    def test_mitre_keeps_the_corner_a_corner(self):
        """A rounded join would put several points where the boundary has one; a mitre puts exactly one."""
        pts = self.vee(90.0, arm=50.0)
        q = corner.offset_row(pts, 10.0, side=1.0)
        self.assertEqual(len(q), len(pts))
        k = pts.index((0.0, 0.0))
        self.assertAlmostEqual(math.dist(q[k], (0.0, 0.0)), 10.0 * math.sqrt(2), places=9)

    def test_mitre_length_grows_with_the_turn(self):
        for deg, want in ((0.0, 1.0), (60.0, 1 / math.cos(math.radians(30))), (90.0, math.sqrt(2))):
            pts = self.vee(deg, arm=50.0)
            q = corner.offset_row(pts, 1.0, side=1.0)
            k = pts.index((0.0, 0.0))
            self.assertAlmostEqual(math.dist(q[k], (0.0, 0.0)), want, places=9)

    def test_plan_row_merges_squeezed_edges_and_splits_stretched_ones(self):
        tight = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]
        blocks, up = corner.plan_row(tight, 5.0)
        self.assertIn((3, 1), blocks)
        wide = [(0.0, 0.0), (20.0, 0.0)]
        blocks, up = corner.plan_row(wide, 5.0)
        self.assertEqual(blocks, [(1, 3)])
        self.assertEqual(len(up), 4)

    def test_blocks_that_change_the_count_keep_the_parity_rule(self):
        """(1,1), (3,1) and (1,3) leave the boundary edge count even; a lone (2,1) does not close."""
        with self.assertRaises(ValueError):
            corner.units_of([(1, 1), (2, 1), (1, 1)])
        self.assertEqual(corner.units_of([(2, 1), (2, 1)]), ["T2P"])
        self.assertEqual(corner.units_of([(1, 1), (3, 1), (1, 3)]), ["Q", "T3", "R3"])

    def test_both_real_corners_both_senses_are_all_quad_and_valid(self):
        """A shallow and a steep tangent break of a weld root, as real roots have them. Each is built with the band on
        the side the corner opens and on the side it closes: those are different problems."""
        for deg in (8.0, 64.0):
            for side in (1.0, -1.0):
                m, zones, info = corner.band(self.vee(deg), self.ROWS, side=side)
                corner.smooth(m, passes=10)
                c = check_all(m)
                rows = [quality.metrics(m.corners(q)) for q in sorted(m.quads)]
                cj = min(r["corner_jacobian"] for r in rows)
                where = "%.3f deg, side %+.0f" % (deg, side)
                self.assertEqual(c["duplicate_node_elements"], [], where)
                self.assertEqual(c["zero_area_elements"], [], where)
                self.assertEqual(c["inverted_elements"], [], where)
                self.assertEqual(c["hanging_nodes"], [], where)
                self.assertEqual(c["non_manifold_edges"], [], where)
                self.assertEqual(c["orphan_nodes"], [], where)
                self.assertEqual(corner.self_intersections(m), [], where)
                self.assertTrue(c["euler_ok"], where)
                self.assertGreater(cj, 0.0, where)

    def test_the_boundary_is_reproduced_not_smoothed(self):
        """Every root node, the corner included, is still exactly where the boundary put it after smoothing."""
        root = self.vee(64.0)
        m, _, _ = corner.band(root, self.ROWS, side=1.0)
        corner.smooth(m, passes=10)
        for p in root:
            self.assertIn(p, set(m.nodes.values()))

    def test_a_folded_band_is_caught(self):
        """The failure a normal offset produces at a corner: the row crosses itself. It has to be detected, not
        left to the element checks, which a folded but individually valid row can pass."""
        m = Mesh()
        a = [m.node(0.0, 0.0), m.node(10.0, 0.0), m.node(20.0, 0.0)]
        b = [m.node(0.0, 10.0), m.node(-30.0, -5.0), m.node(20.0, 10.0)]
        m.quad(a[0], a[1], b[1], b[0])
        m.quad(a[1], a[2], b[2], b[1])
        self.assertTrue(corner.self_intersections(m))

    def test_smoothing_pins_the_boundary_and_never_makes_it_worse(self):
        m, _, _ = corner.band(self.vee(64.0), self.ROWS, side=1.0)
        before = min(quality.metrics(m.corners(q))["corner_jacobian"] for q in m.quads)
        frozen = {i: m.nodes[i] for a, b in boundary_edges(m) for i in (a, b)}
        info = corner.smooth(m, passes=10)
        after = min(quality.metrics(m.corners(q))["corner_jacobian"] for q in m.quads)
        self.assertGreaterEqual(after, before)
        self.assertEqual(info["pinned_nodes"], len(frozen))
        for i, p in frozen.items():
            self.assertEqual(m.nodes[i], p)


class TestWedgeMetric(unittest.TestCase):
    """CHEXA and CPENTA each measured against their own ideal, so the two are comparable."""

    def test_an_ideal_element_of_either_type_scores_one(self):
        cube = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
        self.assertAlmostEqual(solid.corner_scaled_jacobian(cube, solid.HEX_CORNERS, solid.HEX_IDEAL), 1.0)
        h = math.sqrt(3) / 2
        wedge = [(0, 0, 0), (1, 0, 0), (0.5, h, 0), (0, 0, 1), (1, 0, 1), (0.5, h, 1)]
        self.assertAlmostEqual(solid.corner_scaled_jacobian(wedge, solid.PENTA_CORNERS, solid.PENTA_IDEAL), 1.0)

    def test_without_the_wedge_ideal_a_perfect_wedge_reads_thirteen_percent_low(self):
        h = math.sqrt(3) / 2
        wedge = [(0, 0, 0), (1, 0, 0), (0.5, h, 0), (0, 0, 1), (1, 0, 1), (0.5, h, 1)]
        self.assertAlmostEqual(solid.corner_scaled_jacobian(wedge, solid.PENTA_CORNERS, 1.0), h)

    def test_each_kind_is_reported_on_its_own(self):
        s = solid.Solid()
        for p in [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]:
            s.add_node(*p)
        s.elements = [("hex8", list(range(1, 9)))]
        r = solid.check_solid(s)
        self.assertEqual(sorted(r["scaled_jacobian_by_kind"]), ["hex8"])
        self.assertAlmostEqual(r["scaled_jacobian_by_kind"]["hex8"]["min"], 1.0)
        self.assertEqual(r["collapsed_elements"], [])


class TestBlocks(unittest.TestCase):
    """Mapped four-sided blocks: what carries the mesh from the transition band out to the rest of a part."""

    @staticmethod
    def square(mesh, n, m, x0=0.0, y0=0.0, w=1.0, h=1.0):
        b = [mesh.node(x0 + w * i / n, y0) for i in range(n + 1)]
        t = [mesh.node(x0 + w * i / n, y0 + h) for i in range(n + 1)]
        l = [mesh.node(x0, y0 + h * j / m) for j in range(m + 1)]
        r = [mesh.node(x0 + w, y0 + h * j / m) for j in range(m + 1)]
        return b, t, l, r

    def test_a_square_block_is_a_regular_grid(self):
        mesh = Mesh()
        b, t, l, r = self.square(mesh, 4, 3, w=8.0, h=6.0)
        made = block.tfi(mesh, b, t, l, r)
        self.assertEqual(len(made), 12)
        c = check_all(mesh)
        self.assertTrue(c["ok"])
        for q in mesh.quads:
            self.assertAlmostEqual(quality.metrics(mesh.corners(q))["corner_jacobian"], 1.0)
            self.assertAlmostEqual(abs(mesh.area(q)), 4.0)

    def test_two_blocks_sharing_a_side_share_its_nodes(self):
        """The reason for blocks at all: a shared side is shared node for node, so there is no hanging node."""
        mesh = Mesh()
        b1, t1, l1, r1 = self.square(mesh, 3, 2)
        block.tfi(mesh, b1, t1, l1, r1)
        b2, t2, l2, r2 = self.square(mesh, 3, 2, y0=1.0)
        self.assertEqual(b2, t1)
        block.tfi(mesh, b2, t2, l2, r2)
        c = check_all(mesh)
        self.assertEqual(c["hanging_nodes"], [])
        self.assertTrue(c["euler_ok"])
        self.assertEqual(c["elements"], 12)

    def test_mismatched_sides_are_refused_not_guessed(self):
        mesh = Mesh()
        b, t, l, r = self.square(mesh, 3, 2)
        with self.assertRaises(ValueError):
            block.tfi(mesh, b, t[:-1], l, r)
        with self.assertRaises(ValueError):
            block.tfi(mesh, b, t, l, r[:-1] + [mesh.node(99.0, 99.0)])

    def test_discretize_puts_equal_arc_between_nodes_and_keeps_the_ends(self):
        poly = [(0.0, 0.0), (3.0, 0.0), (3.0, 4.0)]
        p = block.discretize(poly, 7)
        self.assertEqual(p[0], poly[0])
        self.assertEqual(p[-1], poly[-1])
        d = [math.dist(p[i], p[i + 1]) for i in range(7)]
        self.assertTrue(max(d) - min(d) < 1e-9, d)

    def test_project_point_lands_on_the_segment_not_its_ends(self):
        poly = [(0.0, 0.0), (10.0, 0.0)]
        self.assertEqual(block.project_point(poly, (3.0, 5.0)), (3.0, 0.0))
        self.assertEqual(block.project_point(poly, (-4.0, 5.0)), (0.0, 0.0))


class TestSlidingSmooth(unittest.TestCase):
    def test_a_node_may_slide_along_a_named_curve_but_not_leave_it(self):
        """Pinning every boundary node is right for the weld root and wrong for a plain straight end: a row squeezed
        against the end has nowhere to go. Sliding keeps the shape and changes only the spacing."""
        mesh = Mesh()
        b, t, l, r = TestBlocks.square(mesh, 4, 3, w=8.0, h=6.0)
        block.tfi(mesh, b, t, l, r)
        end = [(0.0, 0.0), (0.0, 6.0)]
        mesh.nodes[l[1]] = (0.0, 0.2)                          # crowd two nodes at the bottom of the left end
        before = [mesh.nodes[i] for i in l]
        info = corner.smooth(mesh, passes=15, slide=[end])
        self.assertEqual(info["sliding_nodes"], 2)             # the two interior nodes of the end, not its corners
        for i in l:
            self.assertAlmostEqual(mesh.nodes[i][0], 0.0)      # still on the end line
        self.assertGreater(mesh.nodes[l[1]][1], before[1][1])  # and it has spread back out
        self.assertEqual(mesh.nodes[l[0]], (0.0, 0.0))
        self.assertEqual(mesh.nodes[l[-1]], (0.0, 6.0))


class TestDeck(unittest.TestCase):
    """The deck has to open beside the one it replaces, so it is written in the same dialect."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.s = solid.Solid()
        for p in [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]:
            self.s.add_node(*p)
        self.s.elements = [("hex8", list(range(1, 9)))]

    def tearDown(self):
        self.d.cleanup()

    def test_ids_start_where_they_are_told_to(self):
        p = os.path.join(self.d.name, "a.bdf")
        r = nastran.write_deck(self.s, p, node_base=27501, elem_base=20613, comp_id=1, comp_name="c")
        self.assertEqual(r["node_id_range"], (27501, 27508))
        self.assertEqual(r["element_id_range"], (20613, 20613))
        text = io.open(p, encoding="ascii").read()
        self.assertIn("$HMCOMP ID", text)
        self.assertIn('$HMNAME COMP', text)
        self.assertIn("BEGIN BULK", text)
        self.assertTrue(text.rstrip().endswith("ENDDATA"))

    def test_every_field_is_eight_characters(self):
        p = os.path.join(self.d.name, "a.bdf")
        nastran.write_deck(self.s, p, node_base=1, elem_base=1)
        for line in io.open(p, encoding="ascii"):
            line = line.rstrip("\r\n")
            if line.startswith(("GRID", "CHEXA", "CPENTA", "+")):
                self.assertEqual(len(line) % 8, 0, line)

    def test_f8_keeps_as_many_digits_as_fit(self):
        self.assertEqual(nastran.f8(-1819.13), "-1819.13")
        self.assertEqual(len(nastran.f8(-42.7531490001)), 8)
        self.assertEqual(nastran.f8(0.0), "     0.0")
        self.assertLessEqual(abs(float(nastran.f8(1234.56789)) - 1234.56789), 0.01)

    def test_the_deck_reads_back_as_the_same_mesh(self):
        p = os.path.join(self.d.name, "a.bdf")
        nastran.write_deck(self.s, p, node_base=1, elem_base=1)
        nodes, elems = face.read_solid_bdf(p)
        self.assertEqual(len(nodes), 8)
        self.assertEqual(len(elems), 1)
        self.assertLess(nastran.round_trip_error(self.s, p), 1e-6)


class TestWebCounts(unittest.TestCase):
    """The far-field row count: follows the local height, and changes only in steps a quad mesh can make."""

    def test_rows_follow_height_and_step_by_two(self):
        from mesh_transition import web
        h = [80.0 + 3.0 * k for k in range(120)]
        n = web.row_counts(h, 14.5, 13.0)
        self.assertEqual(len({x % 2 for x in n}), 1)
        self.assertTrue(all(abs(a - b) in (0, 2) for a, b in zip(n, n[1:])))
        self.assertLess(n[0], n[-1])
        for x, c in zip(h, n):
            self.assertGreaterEqual(x / c, 13.0 * 0.999)       # never finer than coarse

    def test_no_row_change_at_the_plate_ends(self):
        from mesh_transition import web
        h = [60.0 + 10.0 * k for k in range(30)]
        n = web.row_counts(h, 14.5, 13.0, ends_fixed=3)
        self.assertEqual(len(set(n[:4])), 1)
        self.assertEqual(len(set(n[-4:])), 1)

    def test_a_side_on_t3_joins_columns_that_differ_by_two(self):
        from mesh_transition import web
        for a, b in ((6, 6), (6, 8), (8, 6), (1, 3), (3, 1)):
            m = Mesh()
            left = [m.node(0.0, 14.0 * a * j / a) for j in range(a + 1)]
            right = [m.node(13.0, 14.0 * a * j / b) for j in range(b + 1)]
            corner.ladder(m, left, right, web.column_blocks(a, b), 0.5, flip=True)
            c = check_all(m)
            self.assertTrue(c["ok"], (a, b, c))
        with self.assertRaises(ValueError):
            web.column_blocks(6, 7)
