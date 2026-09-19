# -*- coding: utf-8 -*-
"""build: Qt qCompress XML round trip, trajectory patch, contact table generation (no Simufact)."""
import io
import os
import shutil
import sys
import tempfile
import unittest

import yaml

from tests._paths import TOOL, CASE_DIR, RC, needs_private, needs_rc, private_case, private_path
from common.qxml import qdecode, qencode, robot_blobs, read_text, replace_blobs, strip_blobs

sys.path.insert(0, os.path.join(TOOL, "build"))
import patch_trajectory  # noqa: E402
import contact_table_gen  # noqa: E402

SYNTHETIC_ROBOT = ("<robot_data><name display_name=\"Rob-ring\" internal_name=\"Robot-1\"/>"
                   "<trajectory><connect_to_nodes>false</connect_to_nodes></trajectory></robot_data>")


def synthetic_robots_file(d):
    """A robots_properties.xml in Simufact's layout (one qCompress'd <robot>), written from a made-up robot."""
    p = os.path.join(d, "robots_properties.xml")
    io.open(p, "w", encoding="utf-8", newline="").write(
        '<?xml version="1.0" encoding="UTF-8"?>\n<RobotsPropertiesFile version="2024.2">\n    <robots>\n'
        '        <robot compressed="true">%s</robot>\n    </robots>\n</RobotsPropertiesFile>\n' % qencode(SYNTHETIC_ROBOT))
    return p


class TestQxml(unittest.TestCase):
    def test_roundtrip(self):
        text = "<data><name display_name=\"焊枪\"/>" + "x" * 5000 + "</data>"
        self.assertEqual(qdecode(qencode(text)), text)

    def test_synthetic_robots_file(self):
        with tempfile.TemporaryDirectory() as d:
            blobs = robot_blobs(synthetic_robots_file(d))
        self.assertEqual(blobs, [SYNTHETIC_ROBOT])
        self.assertIn("<connect_to_nodes>false</connect_to_nodes>", blobs[0])

    def test_identity_replace_keeps_outer(self):
        with tempfile.TemporaryDirectory() as d:
            src = read_text(synthetic_robots_file(d))
        out, before, after = replace_blobs(src, lambda x, i: x)
        self.assertEqual(strip_blobs(src), strip_blobs(out))
        self.assertEqual(before, after)

    @needs_private
    def test_private_reference_robots(self):
        blobs = robot_blobs(os.path.join(CASE_DIR, "reference", "Proc", "robots_properties.xml"))
        self.assertEqual(len(blobs), 2)
        self.assertIn("<connect_to_nodes>true</connect_to_nodes>", blobs[0])


class TestPatchTrajectory(unittest.TestCase):
    @needs_private
    @needs_rc
    def test_patch_from_case_settings(self):
        case = private_case()
        settings = {"Rob-" + s["label"]: s["trajectory"] for s in case["sides"]}
        with tempfile.TemporaryDirectory() as d:
            proc = os.path.join(d, "proj", "Proc")
            os.makedirs(proc)
            shutil.copy2(os.path.join(RC, "A3_nopatch_280s", "Proc", "robots_properties.xml"), proc)
            # A3 robots are named Rob-w001 / Rob-w002 and carry the default (unpatched) block
            after = patch_trajectory.patch(proc, settings, log=lambda *_: None)
            blobs = robot_blobs(os.path.join(proc, "robots_properties.xml"))
            self.assertEqual(blobs, after)
            self.assertIn("<orientation_mode>Original orientation</orientation_mode>", blobs[1])
            self.assertIn('<variation_angle dimension="1" unit="1" value="45.0"/>', blobs[1])
            self.assertIn("<connect_to_nodes>true</connect_to_nodes>", blobs[0])


class TestContactTable(unittest.TestCase):
    @needs_private
    def test_generate_equals_reference_except_names(self):
        case = private_case()
        rs = yaml.safe_load(io.open(os.path.join(TOOL, "build", "rulesets", "fillet.yaml"), encoding="utf-8"))
        refmap = case["reference"]["contact_name_map"]
        internal = {role: name for name, role in refmap.items()}
        web, plate = (case["base"]["web_name"], internal["web"]), (case["base"]["plate_name"], internal["plate"])
        torches = [("Rob-w001", "Rob-w001"), ("Rob-w002", "Rob-w002")]
        xml, n = contact_table_gen.generate(rs, web, plate, torches)
        self.assertEqual(n, 12)
        roles = {"Rob-w001": "torch1", "Rob-w002": "torch2", web[1]: "web", plate[1]: "plate"}
        name_only, other, tdiff = contact_table_gen.compare(xml, private_path(case["reference"]["contact_table_xml"]),
                                                            roles, refmap, torches, web, plate)
        self.assertEqual(other, [])
        self.assertEqual(tdiff, [])
        self.assertEqual(len(name_only), 12)

    def test_write_sets_user_defined(self):
        rs = yaml.safe_load(io.open(os.path.join(TOOL, "build", "rulesets", "fillet.yaml"), encoding="utf-8"))
        xml, _ = contact_table_gen.generate(rs, ("w", "W"), ("p", "P"), [("a", "A"), ("b", "B")])
        with tempfile.TemporaryDirectory() as d:
            proc = os.path.join(d, "proj", "Proc")
            os.makedirs(proc)
            io.open(os.path.join(proc, "Proc_properties.xml"), "w", encoding="utf-8").write(
                "<wlProcess>\n    <contact_table>\n        <user_defined>false</user_defined>\n    </contact_table>\n</wlProcess>\n")
            io.open(os.path.join(proc, "robots_properties.xml"), "w", encoding="utf-8").write("<RobotsPropertiesFile/>\n")
            contact_table_gen.write(proc, xml, log=lambda *_: None)
            self.assertIn("<user_defined>true</user_defined>", io.open(os.path.join(proc, "Proc_properties.xml"), encoding="utf-8").read())
            self.assertTrue(os.path.isfile(os.path.join(proc, "_contact_table", "properties.xml")))


if __name__ == "__main__":
    unittest.main()
