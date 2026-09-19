# -*- coding: utf-8 -*-
"""build step, offline: generate Proc\\_contact_table\\properties.xml from a rule set (rulesets/<joint>.yaml, R22)
and set <contact_table><user_defined>true in the Proc properties. Copied from P0_A4b contact_table_gen.py; change:
rules read from YAML instead of being inline. A reference contact table is only parsed for a field-by-field
comparison (--compare), never copied.
Usage: python contact_table_gen.py <Proc dir> --ruleset fillet.yaml --web "disp|internal" --plate "disp|internal"
         --torch "disp|internal" --torch "disp|internal" [--compare ref.xml --map refInternal=torch1|torch2|web|plate ...] [--dry]"""
import argparse
import difflib
import io
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET

import yaml

EL = {
    "constant": """                <electrical_conductivity>
                    <Mode>1</Mode>
                    <Constant dimension="47" unit="0" unit_symbol="1/(Ω·m²)" Version="1">0.0</Constant>
                </electrical_conductivity>
""",
    "coating": """                <electrical_conductivity>
                    <Mode>4</Mode>
                </electrical_conductivity>
                <film_thickness>1e-04</film_thickness>
                <coating_resistivity>
                    <Mode>1</Mode>
                    <Constant dimension="43" unit="0" unit_symbol="Ω·m" Version="1">0.0</Constant>
                </coating_resistivity>
"""}


def contact(rule, first, second, active):
    return """        <contact active="%s" in_distance="%s">
            <type>%s</type>
            <touching_body>
                <name display_name="%s" internal_name="%s"/>
            </touching_body>
            <contacted_body>
                <name display_name="%s" internal_name="%s"/>
            </contacted_body>
            <local_joint>false</local_joint>
            <properties>
                <direction>%s</direction>
                <type>%s</type>
                <tolerance>2e-05</tolerance>
                <tolerance_automatic>true</tolerance_automatic>
                <bias>%s</bias>
                <bias_automatic>true</bias_automatic>
                <separation_threshold_force>0</separation_threshold_force>
                <separation_threshold_stress>0</separation_threshold_stress>
                <separation_threshold_flowstress>0</separation_threshold_flowstress>
                <separation_automatic>true</separation_automatic>
                <near_tolerance>0</near_tolerance>
                <near_tolerance_flag>false</near_tolerance_flag>
                <interference_closure>0</interference_closure>
                <interference_closure_flag>false</interference_closure_flag>
                <friction_stress_limit>1e+20</friction_stress_limit>
                <initial_stress_free_projection>%s</initial_stress_free_projection>
                <glue_on_peak_temp_mode>auto</glue_on_peak_temp_mode>
%s            </properties>
        </contact>
""" % (("true" if active else "false"), ("true" if rule["in_distance"] else "false"), rule["type"], first[0], first[1],
       second[0], second[1], rule["direction"], rule["glue"], rule["bias"], rule["stress_free_projection"], EL[rule["electrical"]])


def generate(ruleset, web, plate, torches):
    R = ruleset["rules"]
    parts = []
    for t in torches:
        parts.append(contact(R["torch_self"], t, t, R["torch_self"]["active"]))
    for t in torches:
        for c in (web, plate):
            parts.append(contact(R["torch_component"], t, c, R["torch_component"]["active"]))
    for i, t in enumerate(torches):
        for j, u in enumerate(torches):
            if i != j:
                parts.append(contact(R["torch_torch"], t, u, R["torch_torch"]["active"]))
    for c in (web, plate):
        parts.append(contact(R["component_self"], c, c, R["component_self"]["active"]))
    cc = R["component_component"]
    parts.append(contact(cc, web, plate, cc["active_first_to_second"]))
    parts.append(contact(cc, plate, web, cc["active_second_to_first"]))
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<contact_table>\n    <outdated>true</outdated>\n    <contacts>\n' + "".join(parts) + "    </contacts>\n</contact_table>\n"
    ET.fromstring(xml.encode("utf-8"))
    return xml, len(parts)


def compare(xml, ref_path, roles, refmap, torches, web, plate):
    """Returns (name_only_diffs, other_diffs, text_diff_lines_after_name_substitution)."""
    def flat(root, rm):
        out = []
        for c in root.find("contacts").findall("contact"):
            rec = {"active": c.get("active"), "in_distance": c.get("in_distance"), "type": c.findtext("type"), "local_joint": c.findtext("local_joint")}
            for side in ("touching_body", "contacted_body"):
                n = c.find(side + "/name")
                rec[side] = rm.get(n.get("internal_name"), "?" + n.get("internal_name"))
                rec[side + "_names"] = (n.get("display_name"), n.get("internal_name"))
            props = c.find("properties")
            for e in props.iter():
                if e is not props:
                    rec["properties/" + e.tag] = ((e.text or "").strip(), tuple(sorted(e.attrib.items())))
            out.append(rec)
        return out

    gen, ref = flat(ET.fromstring(xml.encode("utf-8")), roles), flat(ET.parse(ref_path).getroot(), refmap)
    diffs = [] if len(gen) == len(ref) else ["contact count %d vs %d" % (len(gen), len(ref))]
    for i, (g, r) in enumerate(zip(gen, ref), 1):
        for k in sorted(set(g) | set(r)):
            if g.get(k) != r.get(k):
                diffs.append("contact %d %s: generated %s / reference %s%s" % (i, k, g.get(k), r.get(k), "  [name only]" if k.endswith("_names") else ""))
    name_only = [d for d in diffs if d.endswith("[name only]")]
    other = [d for d in diffs if not d.endswith("[name only]")]
    sub = io.open(ref_path, encoding="utf-8").read()
    target = {"torch1": torches[0], "torch2": torches[1], "web": web, "plate": plate}
    for c in ET.parse(ref_path).getroot().find("contacts").findall("contact"):
        for side in ("touching_body", "contacted_body"):
            n = c.find(side + "/name")
            t = target[refmap[n.get("internal_name")]]
            sub = sub.replace('display_name="%s" internal_name="%s"' % (n.get("display_name"), n.get("internal_name")),
                              'display_name="%s" internal_name="%s"' % t)
    tdiff = [l for l in difflib.unified_diff(sub.splitlines(), xml.splitlines(), n=0, lineterm="") if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    return name_only, other, tdiff


def write(proc_dir, xml, log=print):
    root = os.path.dirname(os.path.normpath(proc_dir))
    if os.path.exists(os.path.join(root, "project.lock")):
        raise RuntimeError("project.lock present")
    props = [f for f in os.listdir(proc_dir) if f.endswith("_properties.xml") and f != "robots_properties.xml"]
    if len(props) != 1:
        raise RuntimeError("Proc *_properties.xml: %r" % props)
    PP = os.path.join(proc_dir, props[0])
    src = io.open(PP, encoding="utf-8", newline="").read()
    new, n = re.subn(r"(<contact_table>\s*<user_defined>)false(</user_defined>)", r"\1true\2", src, count=1)
    if n != 1 and "<user_defined>true</user_defined>" not in src:
        raise RuntimeError("contact_table/user_defined not found")
    d = [l for l in difflib.unified_diff(src.splitlines(), new.splitlines(), n=0, lineterm="") if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    if not (len(d) in (0, 2) and all("user_defined" in l for l in d)):
        raise RuntimeError("Proc properties self-check failed: %s" % d)
    CT = os.path.join(proc_dir, "_contact_table", "properties.xml")
    os.makedirs(os.path.dirname(CT), exist_ok=True)
    if os.path.exists(CT) and not os.path.exists(CT + ".bak_before_ctgen"):
        shutil.copy2(CT, CT + ".bak_before_ctgen")
    io.open(CT, "w", encoding="utf-8", newline="\n").write(xml)
    if not os.path.exists(PP + ".bak_before_ctgen"):
        shutil.copy2(PP, PP + ".bak_before_ctgen")
    io.open(PP, "w", encoding="utf-8", newline="").write(new)
    log("WRITTEN %s" % CT)
    log("WRITTEN %s (user_defined=true)" % PP)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("proc")
    p.add_argument("--ruleset", required=True)
    p.add_argument("--web", required=True)
    p.add_argument("--plate", required=True)
    p.add_argument("--torch", action="append", required=True)
    p.add_argument("--compare", default=None)
    p.add_argument("--map", action="append", default=[])
    p.add_argument("--dry", action="store_true")
    a = p.parse_args()
    split = lambda s: tuple(s.split("|", 1)) if "|" in s else (s, s)
    web, plate, torches = split(a.web), split(a.plate), [split(t) for t in a.torch]
    ruleset = yaml.safe_load(io.open(a.ruleset, encoding="utf-8"))
    if ruleset.get("mode") == "process_default":
        print("process_default=true; contact table left to Simufact; expected bodies=%s" % ruleset.get("expected_bodies"))
        return
    xml, n = generate(ruleset, web, plate, torches)
    print("generated contacts:", n)
    if a.compare:
        roles = {torches[0][1]: "torch1", torches[1][1]: "torch2", web[1]: "web", plate[1]: "plate"}
        name_only, other, tdiff = compare(xml, a.compare, roles, dict(m.split("=", 1) for m in a.map), torches, web, plate)
        print("field-by-field comparison (roles mapped): %d name-only differences, %d other differences" % (len(name_only), len(other)))
        for d in name_only + other:
            print("   " + d)
        print("text diff after name substitution: %d lines" % len(tdiff))
        if other or tdiff:
            sys.exit("!! differences against the reference beyond names, nothing written")
    if a.dry:
        print("dry run, nothing written")
        return
    write(a.proc, xml)


if __name__ == "__main__":
    main()
