# -*- coding: utf-8 -*-
"""Rank nearby welding cases and optionally emit a case.json draft."""
import argparse
import csv
import io
import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(HERE, "参数库_v1.csv")
RELIABLE_THRESHOLD = 90.0


def plain(value):
    """Value before the first provenance parenthesis."""
    return (value or "").split("（", 1)[0].strip()


def material_category(value):
    s = plain(value).lower()
    if any(x in s for x in ("铝", "aluminium", "aluminum", "aa", "6a", "5a")):
        return "aluminum"
    if any(x in s for x in ("不锈", "stainless", "304", "316")):
        return "stainless"
    return "carbon_steel"


def joint_matches(query, value):
    q, s = query.lower(), plain(value).lower()
    aliases = {
        "fillet": ("角焊", "fillet"),
        "butt": ("对接", "butt"),
        "ring": ("环焊", "circumferential", "ring"),
    }
    tokens = aliases.get(q, (q,))
    return any(t in s for t in tokens)


def numbers(value):
    head = plain(value)
    return [float(x) for x in re.findall(r"(?:(?:=)|(?:^)|(?:[；;/,]))\s*(-?\d+(?:\.\d+)?)", head)]


def first_number(value):
    m = re.search(r"-?\d+(?:\.\d+)?", plain(value))
    return None if not m else float(m.group())


def thickness_penalty(query, value):
    scrubbed = re.sub(r"（[^（）]*）", "", value or "")
    got = [float(x) for x in re.findall(r"=\s*(-?\d+(?:\.\d+)?)", scrubbed)]
    if not got:
        got = numbers(value)
    if not got:
        return 30.0
    q = sorted(float(x) for x in query)
    g = sorted(got)
    if len(q) != len(g):
        return 20.0 + 20.0 * abs(len(q) - len(g))
    return 40.0 * sum(abs(math.log(max(a, 1e-9) / max(b, 1e-9))) for a, b in zip(q, g)) / len(q)


def confidence(value):
    s = value or ""
    if "已校核" in s:
        return "已校核", 20.0
    if "计划" in s:
        return "计划", -50.0
    return "估算", 0.0


def solver_exit(value):
    m = re.search(r"\b(30\d\d)\b", value or "")
    return None if not m else int(m.group(1))


def rank(rows, joint, process, material, thickness):
    out = []
    for row in rows:
        if not joint_matches(joint, row.get("joint_type", "")):
            continue
        score = 100.0
        if plain(row.get("welding_process", "")).lower() != process.lower():
            score -= 40.0
        if material_category(row.get("base_material", "")) != material_category(material):
            score -= 30.0
        score -= thickness_penalty(thickness, row.get("thickness_mm", ""))
        level, delta = confidence(row.get("confidence", ""))
        score += delta
        if solver_exit(row.get("solver_exit", "")) != 3004:
            score -= 30.0
        item = dict(row)
        item["_score"] = score
        item["_confidence"] = level
        out.append(item)
    return sorted(out, key=lambda x: (-x["_score"], x["case_id"]))


def critical(row):
    return {
        "heat_source": {
            "af_mm": first_number(row["hs_af_mm"]), "ar_mm": first_number(row["hs_ar_mm"]),
            "b_mm": first_number(row["hs_b_mm"]), "d_mm": first_number(row["hs_d_mm"]),
            "gauss_M": first_number(row["hs_gauss_M"]), "power": plain(row["hs_power"]),
        },
        "weld_zone_mm": plain(row["size_weld_zone_mm"]),
        "section_spacing": plain(row["bead_spec"]),
        "time_step": plain(row["weld_step_rule"]),
        "constraint": plain(row["constraint"]),
    }


def case_draft(row):
    p = critical(row)
    return {
        "_draft_from": row["case_id"],
        "_todo": ["选择 geometry_family", "补齐所有输入和参照路径", "核对工艺 XML、热源 XML 与约束"],
        "case_name": "TODO", "geometry_family": "TODO", "output_dir": "",
        "base": {"web_bdf": "", "plate_bdf": "", "web_name": "TODO", "plate_name": "TODO"},
        "materials": {"base_xmt": "", "filler_xmt": ""},
        "heat_source": {"xml": "", "entity_name": "TODO", "parameters": p["heat_source"]},
        "bead": {"leg_mm": first_number(row["leg_length_mm"]), "section_spacing_source": p["section_spacing"]},
        "process": {"parameters_xml": "", "weld_speed_mm_s": first_number(row["weld_speed_mm_s"]), "time_step_source": p["time_step"]},
        "constraints": {"source": p["constraint"], "files": []},
        "reference": {"dat": "", "proc_dir": "", "results": {}},
    }


def format_table(rows):
    if not rows:
        return "无可靠匹配，以下仅供参考\n（接头类型不同，已全部排除）"
    lines = []
    if rows[0]["_score"] < RELIABLE_THRESHOLD:
        lines.append("无可靠匹配，以下仅供参考")
    lines += [
        "case_id | 得分 | 热源六参数 | 焊缝区尺寸 | 截面间距 | 步长 | 约束 | 可信度 | 来源文件",
        "---|---:|---|---|---|---|---|---|---",
    ]
    for r in rows:
        p = critical(r)
        hs = p["heat_source"]
        six = "af={af_mm}, ar={ar_mm}, b={b_mm}, d={d_mm}, M={gauss_M}, P={power}".format(**hs)
        vals = [r["case_id"], "%.2f" % r["_score"], six, p["weld_zone_mm"], p["section_spacing"], p["time_step"],
                p["constraint"], r["_confidence"], r["source_files"]]
        lines.append(" | ".join(str(x).replace("|", "\\|").replace("\n", " ") for x in vals))
    return "\n".join(lines)


def run(argv=None):
    ap = argparse.ArgumentParser(prog="weldsim suggest")
    ap.add_argument("--joint", required=True)
    ap.add_argument("--process", required=True)
    ap.add_argument("--material", required=True)
    ap.add_argument("--thickness", required=True, help="comma-separated mm")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--emit-case")
    ap.add_argument("--library", default=DEFAULT_CSV)
    a = ap.parse_args(argv)
    thick = [float(x) for x in a.thickness.split(",")]
    rows = list(csv.DictReader(io.open(a.library, encoding="utf-8-sig", newline="")))
    ranked = rank(rows, a.joint, a.process, a.material, thick)[:max(0, a.n)]
    print(format_table(ranked))
    if a.emit_case:
        if not ranked:
            print("未生成 case.json：没有同接头类型候选")
        else:
            io.open(a.emit_case, "w", encoding="utf-8", newline="\n").write(json.dumps(case_draft(ranked[0]), ensure_ascii=False, indent=2) + "\n")
            print("case.json 草稿: %s" % os.path.abspath(a.emit_case))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
