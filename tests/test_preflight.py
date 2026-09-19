# -*- coding: utf-8 -*-
"""preflight: rule library integrity and one counter-example per block rule (no Simufact, no solver).
Counter-examples start from the private case (WELDSIM_PRIVATE_CASE); rule library and CLI checks are public."""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from tests._paths import TOOL, HAVE_PRIVATE, private_case, private_path
from common.bdf import write_bdf
from common.preflight import run_preflight, load_rules, RULES_YAML


def base_case(out_dir):
    """Private case json with absolute input paths and a fresh output directory."""
    c = private_case()
    for k in ("web_bdf", "plate_bdf"):
        c["base"][k] = private_path(c["base"][k])
    for k in ("base_xmt", "filler_xmt"):
        c["materials"][k] = private_path(c["materials"][k])
    c["heat_source"]["xml"] = private_path(c["heat_source"]["xml"])
    c["process"]["parameters_xml"] = private_path(c["process"]["parameters_xml"])
    c["fixed_nodes_csv"] = private_path(c["fixed_nodes_csv"])
    for s in c["sides"]:
        s["window_csv"] = private_path(s["window_csv"])
    c.pop("reference", None)
    c["output_dir"] = out_dir
    return c


def slab(d, layers, name, small_grid=False):
    nodes, idx = [], {}
    nx, ny = 4, 3
    for k in range(layers + 1):
        for j in range(ny + 1):
            for i in range(nx + 1):
                idx[(i, j, k)] = len(nodes) + 1
                nodes.append((10.0 * i, 10.0 * j, 6.0 * k / layers))
    hexas = []
    for k in range(layers):
        for j in range(ny):
            for i in range(nx):
                hexas.append([idx[(i, j, k)], idx[(i + 1, j, k)], idx[(i + 1, j + 1, k)], idx[(i, j + 1, k)],
                              idx[(i, j, k + 1)], idx[(i + 1, j, k + 1)], idx[(i + 1, j + 1, k + 1)], idx[(i, j + 1, k + 1)]])
    p = os.path.join(d, name)
    write_bdf(p, nodes, hexas)
    if small_grid:
        t = io.open(p, encoding="ascii").read().replace("ENDDATA", "GRID    99999           0.0     0.0     0.0\nENDDATA")
        io.open(p, "w", encoding="ascii").write(t)
    return p


@unittest.skipUnless(HAVE_PRIVATE, "private case data not available (WELDSIM_PRIVATE_CASE)")
class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="pf_")
        self.case = base_case(os.path.join(self.d, "out"))
        self.hs = self.case["heat_source"]["xml"]
        self.pp = self.case["process"]["parameters_xml"]

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_pf(self, only):
        p = os.path.join(self.d, "case.json")
        io.open(p, "w", encoding="utf-8").write(json.dumps(self.case, ensure_ascii=False))
        return run_preflight(p, only=only)

    def assertBlocks(self, res, rule):
        hits = [r for r in res if r.rule == rule and r.level == "block"]
        self.assertTrue(hits, "expected [block] %s, got %s" % (rule, [(r.level, r.rule, r.message) for r in res]))

    def assertNoBlock(self, res):
        self.assertEqual([r for r in res if r.level == "block"], [])

    def copy_with(self, src, name, sub=None):
        t = io.open(src, encoding="utf-8", newline="").read()
        if sub:
            t = sub(t)
        dst = os.path.join(self.d, name)
        io.open(dst, "w", encoding="utf-8", newline="").write(t)
        return dst


class TestRulesYaml(unittest.TestCase):
    def test_all_ids_and_fields(self):
        rules = yaml.safe_load(io.open(RULES_YAML, encoding="utf-8"))["rules"]
        ids = [r["id"] for r in rules]
        self.assertEqual(len(ids), len(set(ids)))
        want = ["R%d" % i for i in range(1, 25)] + ["S%d" % i for i in range(1, 11)] + ["TCL%d" % i for i in range(1, 5)]
        self.assertEqual([i for i in want if i not in ids], [])
        for r in rules:
            self.assertEqual(set(r), {"id", "title", "source", "stage", "enforced_by", "severity"}, r["id"])
            self.assertIn(r["stage"], ("preflight", "prep", "build", "gate", "run", "none"), r["id"])
            self.assertIn(r["severity"], ("block", "warn"), r["id"])

    def test_enforced_by_functions_exist(self):
        for r in load_rules().values():
            if r["enforced_by"] == "manual":
                continue
            f, fn = r["enforced_by"].rsplit(":", 1)
            src = io.open(os.path.join(TOOL, f), encoding="utf-8").read()
            self.assertRegex(src, r"(?m)^def %s\(" % re.escape(fn), r["id"])

    def test_rules_doc_up_to_date(self):
        sys.path.insert(0, os.path.join(TOOL, "tools"))
        import gen_rules_doc
        self.assertEqual(gen_rules_doc.render(), io.open(os.path.join(TOOL, "docs", "rules.md"), encoding="utf-8").read())


class TestPositive(Tmp):
    def test_private_inputs_only_web_layers_warn(self):
        # the private reference web has fewer element layers than PF4 asks for; everything else passes
        res = self.run_pf(None)
        self.assertEqual([(r.rule, r.message) for r in res if r.level == "warn"],
                         [("PF4", "web has fewer than 3 element layers through the thickness")])


class TestCounterExamples(Tmp):
    def test_S3_non_ascii_output(self):
        self.case["output_dir"] = os.path.join(self.d, "输出")
        self.assertBlocks(self.run_pf(["paths"]), "S3")

    def test_S3_space_in_input(self):
        self.case["fixed_nodes_csv"] = shutil.copy(self.case["fixed_nodes_csv"], os.path.join(self.d, "fixed nodes.csv"))
        self.assertBlocks(self.run_pf(["paths"]), "S3")

    def test_PF1_missing_input(self):
        self.case["base"]["web_bdf"] = os.path.join(self.d, "nope.bdf")
        self.assertBlocks(self.run_pf(["exist"]), "PF1")

    def test_PF2_broken_xmt(self):
        p = os.path.join(self.d, "bad.xmt")
        io.open(p, "w", encoding="utf-8").write("<?xml version=\"1.0\"?>\n<sfMaterialData><class>Welding</clas>\n")
        self.case["materials"]["base_xmt"] = p
        self.assertBlocks(self.run_pf(["parse"]), "PF2")

    def test_PF2_wrong_root_heat_source(self):
        self.case["heat_source"]["xml"] = self.copy_with(self.case["materials"]["base_xmt"], os.path.basename(self.hs))
        self.assertBlocks(self.run_pf(["parse"]), "PF2")

    def test_R16_entity_name(self):
        self.case["heat_source"]["entity_name"] = "HS-OTHER-NAME"
        self.assertBlocks(self.run_pf(["heat_source"]), "R16")

    def test_R12_file_name_current(self):
        self.case["heat_source"]["xml"] = shutil.copy(self.hs, os.path.join(self.d, "HS-X-999A25V.xml"))
        res = self.run_pf(["heat_source"])
        self.assertBlocks(res, "R12")
        self.assertFalse([r for r in res if r.rule == "R16" and r.level == "block"])

    def test_R12_xml_voltage(self):
        self.case["heat_source"]["xml"] = self.copy_with(self.hs, os.path.basename(self.hs),
                                                         lambda t: re.sub(r'(<voltage dimension="20" unit="0" value=")([^"]*)"',
                                                                                lambda m: '%s%r"' % (m.group(1), float(m.group(2)) + 4.0), t, count=1))
        self.assertBlocks(self.run_pf(["heat_source"]), "R12")

    def test_S7_cooling_automatic(self):
        def sub(t):
            return re.sub(r'(<step_size_control loadcase_type="2".*?<maximum_time_step[^>]*?)is_automatic="false"', r'\1is_automatic="true"', t, flags=re.S)
        self.case["process"]["parameters_xml"] = self.copy_with(self.pp, "pp.xml", sub)
        self.assertBlocks(self.run_pf(["process"]), "S7")

    def test_S7_cooling_unbounded(self):
        def sub(t):
            return re.sub(r'(<step_size_control loadcase_type="2".*?<maximum_time_step[^>]*?)value="[^"]*"',r'\1value="100.0"', t, flags=re.S)
        self.case["process"]["parameters_xml"] = self.copy_with(self.pp, "pp.xml", sub)
        self.assertBlocks(self.run_pf(["process"]), "S7")

    def test_PF3_weld_not_fixed_step(self):
        self.case["process"]["parameters_xml"] = self.copy_with(self.pp, "pp.xml",
                                                                lambda t: t.replace("<time_steps_method>0</time_steps_method>", "<time_steps_method>1</time_steps_method>"))
        self.assertBlocks(self.run_pf(["process"]), "PF3")

    def test_R21_advance_too_large(self):
        self.case["bead"]["section_spacing_mm"] = 1e-3         # far below any weld speed x time step
        self.assertBlocks(self.run_pf(["process"]), "R21")

    def test_PF4_two_layers(self):
        self.case["base"]["web_bdf"] = slab(self.d, 2, "web2.bdf")
        self.case["base"]["plate_bdf"] = slab(self.d, 3, "plate3.bdf")
        res = self.run_pf(["base_mesh"])
        self.assertEqual([r.message for r in res if r.rule == "PF4" and r.level == "warn"], ["web has fewer than 3 element layers through the thickness"])

    def test_PF4_case_threshold(self):
        self.case.setdefault("preflight", {})["min_layers_through_thickness"] = 2
        res = self.run_pf(["base_mesh"])
        self.assertFalse([r for r in res if r.rule == "PF4" and r.level == "warn"])

    def test_R1_small_field_grid(self):
        self.case["base"]["web_bdf"] = slab(self.d, 3, "web3.bdf", small_grid=True)
        self.case["base"]["plate_bdf"] = slab(self.d, 3, "plate3.bdf")
        self.assertBlocks(self.run_pf(["base_mesh"]), "R1")

    def test_S8_window_no_newline(self):
        p = os.path.join(self.d, "window_w001.csv")
        io.open(p, "wb").write(io.open(self.case["sides"][0]["window_csv"], "rb").read().rstrip(b"\r\n"))
        self.case["sides"][0]["window_csv"] = p
        self.assertBlocks(self.run_pf(["csv"]), "S8")

    def test_S1_target_project_exists(self):
        os.makedirs(os.path.join(self.case["output_dir"], "02_build", self.case["case_name"]))
        self.assertBlocks(self.run_pf(["output"]), "S1")

    def test_S2_project_lock(self):
        d = os.path.join(self.case["output_dir"], "03_gate", "copy_280s")
        os.makedirs(d)
        io.open(os.path.join(d, "project.lock"), "w").write("")
        self.assertBlocks(self.run_pf(["output"]), "S2")


class TestCli(unittest.TestCase):
    def test_preflight_exit_code(self):
        """Public: synthetic case with a wrong heat source entity name must block with exit 1."""
        d = tempfile.mkdtemp(prefix="pf_cli_")
        try:
            src = os.path.join(TOOL, "cases", "example_synthetic.json")
            c = json.load(io.open(src, encoding="utf-8"))
            base = os.path.dirname(src)
            for k in ("web_bdf", "plate_bdf"):
                c["base"][k] = os.path.normpath(os.path.join(base, c["base"][k]))
            c["heat_source"]["xml"] = os.path.normpath(os.path.join(base, c["heat_source"]["xml"]))
            c["heat_source"]["entity_name"] = "wrong"
            c["output_dir"] = os.path.join(d, "out")
            p = os.path.join(d, "case.json")
            io.open(p, "w", encoding="utf-8").write(json.dumps(c, ensure_ascii=False))
            r = subprocess.run([sys.executable, os.path.join(TOOL, "weldsim.py"), "preflight", p], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("[block] R16", r.stdout)
            self.assertRegex(r.stdout, r"summary: block=[1-9]\d* .*-> FAIL")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
