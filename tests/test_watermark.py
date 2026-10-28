# -*- coding: utf-8 -*-
"""build/watermark.py: the text label beside the part (no Simufact).

The label must read the same on every machine (the baked bitmap, not a font), and face the camera of Simufact's home
view (global axes, not the part's) as a caption under the part, clear of it by the gap -- whatever way the part lies."""
import io
import json
import math
import os
import struct
import sys
import tempfile
import unittest

import numpy as np

from tests._paths import TOOL

sys.path.insert(0, os.path.join(TOOL, "build"))
import watermark as W  # noqa: E402
import weldsim  # noqa: E402


def _box(x0, x1, y0, y1, z0, z1, n=5):
    g = [np.linspace(a, b, n) for a, b in ((x0, x1), (y0, y1), (z0, z1))]
    return [tuple(p) for p in np.array(np.meshgrid(*g)).reshape(3, -1).T]


def _t_joint(R=np.eye(3)):
    """A plate 500 x 240 x 10 and a web 8 x 120 standing on it along x, rotated by R."""
    plate = np.array(_box(0, 500, -120, 120, -10, 0)) @ R.T
    web = np.array(_box(0, 500, -4, 4, 0, 120)) @ R.T
    return plate, web


def _volume(tris):
    return sum(np.dot(t[0], np.cross(t[1], t[2])) for t in tris) / 6.0


def _read_stl(path):
    b = io.open(path, "rb").read()
    n = struct.unpack("<I", b[80:84])[0]
    return np.array([struct.unpack("<12f", b[84 + 50 * k: 84 + 50 * k + 48])[3:] for k in range(n)]).reshape(n, 3, 3)


class TestBitmap(unittest.TestCase):
    def test_default_is_baked(self):
        a, src = W.bitmap()
        self.assertEqual(src, "build/watermark_bitmap.py")
        self.assertEqual(a.shape[1], __import__("watermark_bitmap").WIDTH)
        self.assertTrue(a.any())

    def test_baked_matches_default_text(self):
        import watermark_bitmap as B
        self.assertEqual(tuple(B.LINES), W.DEFAULT_LINES)
        self.assertEqual(B.PX_PER_LINE, W.PX_PER_LINE)


class TestSurface(unittest.TestCase):
    def test_closed_with_outward_normals(self):
        a, _ = W.bitmap()
        pitch, thick = 0.5, 1.0
        tris = W.triangles(a, pitch, thick)
        # divergence theorem: a closed, outward-oriented surface encloses exactly the lit pixels times the thickness
        self.assertAlmostEqual(_volume(tris), a.sum() * pitch * pitch * thick, places=6)

    def test_rectangles_cover_the_lit_pixels_once(self):
        a, _ = W.bitmap()
        cover = np.zeros(a.shape, int)
        for c0, r0, c1, r1 in W.rects(a):
            cover[r0:r1, c0:c1] += 1
        self.assertTrue(np.array_equal(cover, a.astype(int)))


class TestPlacement(unittest.TestCase):
    def check(self, part, cfg=None):
        cfg = dict(cfg or {}, gap_mm=30.0)
        with tempfile.TemporaryDirectory() as d:
            info = W.build_label(os.path.join(d, "l.stl"), part, cfg)
            tris = _read_stl(os.path.join(d, "l.stl"))
        u, v, w = W.screen_axes(cfg.get("view", W.DEFAULTS["view"]), cfg.get("screen_up", W.DEFAULTS["screen_up"]))
        pts = tris.reshape(-1, 3)
        # every facet faces the camera
        for t in tris:
            self.assertGreater(np.dot(np.cross(t[1] - t[0], t[2] - t[0]), w), 0.0)
        # under the part on the screen, clear by the gap; centred across it; level with its front
        self.assertAlmostEqual((part @ v).min() - (pts @ v).max(), 30.0, places=3)
        self.assertAlmostEqual((pts @ u).min() + (pts @ u).max(), (part @ u).min() + (part @ u).max(), places=3)
        self.assertAlmostEqual((pts @ w).max(), (part @ w).max(), places=3)
        self.assertTrue(np.allclose(info["axes"], [u, v, w], atol=1e-6))
        return info

    def test_home_view_default(self):
        info = self.check(np.vstack(_t_joint()))
        self.assertEqual(info["axes"], [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])
        self.assertLessEqual(info["size_mm"][0], 0.8 * 500 + 1e-6)

    def test_rotated_part_label_still_faces_the_camera(self):
        c, s = math.cos(0.7), math.sin(0.7)
        Rz = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        Rx = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
        self.check(np.vstack(_t_joint(Rx @ Rz)))

    def test_other_view(self):
        self.check(np.vstack(_t_joint()), {"view": [1.0, -1.0, 0.5], "screen_up": [0, 0, 1]})

    def test_view_along_up_refused(self):
        with self.assertRaises(ValueError):
            W.screen_axes([0, 0, 1], [0, 0, 2])

    def test_small_part_shrinks_label(self):
        part = np.vstack([_box(-25, 25, -6, 0, 0, 100), _box(-3, 3, 0, 50, 0, 100)])
        info = self.check(part)
        self.assertAlmostEqual(info["size_mm"][0], 80.0, places=3)     # 0.8 x the larger screen extent (100 in z)

    def test_flat_label_is_faces_only(self):
        a, _ = W.bitmap()
        self.assertEqual(len(W.triangles(a, 0.5, 0.0)), 2 * len(W.rects(a)))

    def test_same_input_same_file(self):
        part = np.vstack(_t_joint())
        with tempfile.TemporaryDirectory() as d:
            a = W.build_label(os.path.join(d, "a.stl"), part)
            b = W.build_label(os.path.join(d, "b.stl"), part)
        self.assertEqual(a["sha256"], b["sha256"])


class TestCaseSetting(unittest.TestCase):
    def test_defaults_by_role(self):
        self.assertTrue(weldsim.watermark_settings({"role": "project"})["enabled"])
        self.assertFalse(weldsim.watermark_settings({"role": "regression"})["enabled"])

    def test_explicit_switch(self):
        w = weldsim.watermark_settings({"role": "project", "watermark": {"enabled": False}})
        self.assertFalse(w["enabled"])
        self.assertEqual(w["basis"], "case watermark.enabled")

    def test_name_is_ascii(self):
        self.assertTrue(all(ch.isalnum() or ch in "_-" for ch in weldsim.WATERMARK_NAME))
        json.dumps(weldsim.WATERMARK_NAME, ensure_ascii=True)


if __name__ == "__main__":
    unittest.main()
