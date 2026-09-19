# -*- coding: utf-8 -*-
"""Pure read-only data assembly for the Streamlit page."""
from __future__ import annotations

import csv
import io
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"
RUNS = ROOT / "runs"
STEP_DIRS = (
    ("prep", "01_prep"), ("build", "02_build"), ("gate", "03_gate"),
    ("run", "04_run"), ("compare", "05_compare"), ("selfcheck", "06_selfcheck"),
    ("post", "07_post"),
)


def display_path(path) -> str:
    """Path relative to the repository root when it lies inside it (no machine paths on the page)."""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).name


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def _text(path: Path, limit=50000):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text if len(text) <= limit else text[:limit] + "\n\n[日志过长，页面仅显示前 %d 字符]" % limit


def _tail_text(path: Path, limit=20000):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def scan_cases():
    out = []
    for path in sorted(CASES.glob("*.json")):
        data = _json(path)
        out.append({"name": data.get("case_name", path.stem), "path": str(path), "file": path.name})
    return out


def scan_runs(case_name):
    if not RUNS.is_dir():
        return []
    out = []
    for path in sorted(RUNS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_dir() or not (path.name == case_name or path.name.startswith(case_name + "_")):
            continue
        if any((path / d).exists() for _, d in STEP_DIRS):
            out.append(str(path))
    return out


def _manifest(step_dir):
    return _json(step_dir / "manifest.json")


def _status(manifest, step_dir, kind):
    if manifest:
        return "ok" if manifest.get("exit_code") == 0 else "fail"
    if kind == "selfcheck":
        reg = _tail_text(step_dir / "run_regressions.log")
        unit = _tail_text(step_dir / "unit_tests.log")
        if reg or unit:
            return "ok" if ("不一致 0" in reg and re.search(r"\nOK\s*$", unit)) else "fail"
    return "未跑"


def _logs(step_dir, kind):
    patterns = {
        "prep": ("weld_prep_console.log", "hmbatch_mesh_bead.log", "mesh_bead.log", "check_detj.log"),
        "build": ("build_project_console.log", "patch_trajectory.log", "contact_table_gen.log"),
        "gate": ("gate_*.txt",),
        "run": ("start_analysis_console.log", "gate_*.txt"),
        "compare": ("compare_temperature.log", "temperature_section_table.txt"),
        "selfcheck": ("run_regressions.log", "unit_tests.log", "regressions/regression_table.md"),
        "post": ("post_summary.txt", "post.log"),
    }
    files = []
    for pattern in patterns[kind]:
        files.extend(step_dir.glob(pattern))
    seen, blocks = set(), []
    for path in sorted(files, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        blocks.append("===== %s =====\n%s" % (path.name, _text(path)))
    return "\n\n".join(blocks) if blocks else "无日志"


def _prep_metrics(step_dir):
    data = _json(step_dir / "weld_prep_summary.json")
    checks = data.get("checks", {})
    elems = [v.get("detail", {}).get("elements") for k, v in checks.items() if k.startswith("bead elements")]
    detj = [v.get("detail", {}).get("min_detJ") for k, v in checks.items() if k.startswith("R2 detJ")]
    sub = data.get("subband_10um", {})
    return [
        ("焊道单元", sum(x for x in elems if isinstance(x, int)) if elems else "—"),
        ("焊道条数", len(elems) or "—"),
        ("min detJ", "%.6g" % min(detj) if detj else "—"),
        ("10 µm 子带", sum(sub.values()) if sub else 0),
    ]


def _build_metrics(step_dir):
    result = _json(step_dir / "build_result.json")
    text = _text(step_dir / "robots_decoded_after_patch.xml")
    count = len(re.findall(r"<trajectory_modification>", text))
    modes = re.findall(r"<orientation_mode>(.*?)</orientation_mode>", text)
    connects = re.findall(r"<connect_to_nodes>(.*?)</connect_to_nodes>", text)
    calculations = re.findall(r"<calculation>(.*?)</calculation>", text)
    return [
        ("轨迹块", count),
        ("方向模式", " / ".join(modes) or "—"),
        ("连接节点", " / ".join(connects) or "—"),
        ("计算规则", " / ".join(calculations) or "—"),
        ("热源", result.get("heat_source_name", "—")),
    ]


def _gate_metrics(step_dir):
    files = sorted(step_dir.glob("gate_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    text = _text(files[0]) if files else ""
    lines = [s.strip() for s in text.splitlines() if s.startswith("结论:")]
    return [("门禁结论", lines[-1] if lines else "—"), ("白名单", "有" if "白名单放行" in text else "无"),
            ("独立焊道", "是" if "--independent-bead" in text else "—")]


def _run_metrics(manifest):
    solver = manifest.get("solver", {})
    ex = solver.get("exit") or []
    return [("求解退出码", ex[-1] if ex else "—"), ("STS 行", ex[0] if len(ex) > 1 else "—"),
            ("门禁 DAT", "一致" if solver.get("run_dat_equals_gated_dat_except_date") else "否"),
            ("求解墙钟", ("%.1f s" % solver["wall_s"]) if solver.get("wall_s") is not None else "—")]


def _compare_metrics(step_dir):
    data = _json(step_dir / "temperature_comparison.json")
    welds = data.get("welds", {})
    diffs = [v.get("diff_percent") for v in welds.values() if v.get("diff_percent") is not None]
    peaks = [v.get("peak_new_K") for v in welds.values() if v.get("peak_new_K") is not None]
    return [("焊缝数", len(welds) or "—"), ("最大温差", "%.3f%%" % max(diffs) if diffs else "—"),
            ("新峰值", "%.1f K" % max(peaks) if peaks else "—"),
            ("< 5%", "是" if diffs and all(v.get("pass_lt_limit") for v in welds.values()) else "—")]


def _post_metrics(step_dir):
    data = _json(step_dir / "post.json")
    ps = data.get("probes", [])
    peaks = [p["metrics"].get("peak_K") for p in ps if p["metrics"].get("peak_K") is not None]
    t85 = [p["metrics"].get("t85_s") for p in ps if p["metrics"].get("t85_s") is not None]
    far = [p for p in ps if not p["resolution"]["within"]]
    return [("测点数", len(ps) or "—"), ("导出增量", len(data.get("increments_exported", [])) or "—"),
            ("最高峰值", "%.1f K" % max(peaks) if peaks else "—"),
            ("t8/5", "%.2f s" % max(t85) if t85 else "—"),
            ("网格代表不了的点", len(far) if ps else "—")]


def _selfcheck_metrics(step_dir):
    table = _text(step_dir / "regressions" / "regression_table.md") or _text(step_dir / "run_regressions.log")
    unit = _tail_text(step_dir / "unit_tests.log")
    m = re.search(r"合计\s*(\d+)\s*例，一致\s*(\d+)\s*，不一致\s*(\d+)", table)
    t = re.search(r"Ran\s+(\d+)\s+tests", unit)
    return [("回归一致", ("%s/%s" % (m.group(2), m.group(1))) if m else "—"),
            ("回归不一致", m.group(3) if m else "—"), ("单元测试", t.group(1) if t else "—"),
            ("测试结论", "OK" if re.search(r"\nOK\s*$", unit) else "—")]


def preflight_summary(case_name, run_dir):
    candidates = list((ROOT / "validation").glob("**/preflight_%s*.txt" % case_name)) if (ROOT / "validation").exists() else []
    candidates += list(Path(run_dir).glob("preflight*.txt"))
    if candidates:
        path = max(candidates, key=lambda p: p.stat().st_mtime)
        text = _text(path)
        m = re.search(r"summary:\s*block=(\d+)\s+warn=(\d+)\s+ok=(\d+)\s+->\s+(\w+)", text)
        if m:
            return {"block": int(m.group(1)), "warn": int(m.group(2)), "ok": int(m.group(3)), "verdict": m.group(4), "source": display_path(path)}
    build = _manifest(Path(run_dir) / "02_build")
    summary = build.get("preflight", {}).get("summary", {})
    return {"block": int(summary.get("block", 0)), "warn": int(summary.get("warn", 0)), "ok": int(summary.get("ok", 0)),
            "verdict": "FAIL" if summary.get("block") else "PASS", "source": "02_build/manifest.json"}


def assemble_dashboard(case_name, run_dir):
    run = Path(run_dir)
    steps = []
    for kind, dirname in STEP_DIRS:
        step_dir = run / dirname
        manifest = _manifest(step_dir)
        if kind == "prep": metrics = _prep_metrics(step_dir)
        elif kind == "build": metrics = _build_metrics(step_dir)
        elif kind == "gate": metrics = _gate_metrics(step_dir)
        elif kind == "run": metrics = _run_metrics(manifest)
        elif kind == "compare": metrics = _compare_metrics(step_dir)
        elif kind == "post": metrics = _post_metrics(step_dir)
        else: metrics = _selfcheck_metrics(step_dir)
        steps.append({"key": kind, "directory": dirname, "status": _status(manifest, step_dir, kind),
                      "duration_s": manifest.get("duration_s"), "metrics": metrics, "logs": _logs(step_dir, kind)})
    return {"case_name": case_name, "run_dir": str(run), "preflight": preflight_summary(case_name, run), "steps": steps}


def suggest_rows(joint, process, material, thickness, n=3):
    from library.suggest import DEFAULT_CSV, RELIABLE_THRESHOLD, critical, rank
    rows = list(csv.DictReader(io.open(DEFAULT_CSV, encoding="utf-8-sig", newline="")))
    ranked = rank(rows, joint, process, material, [float(x.strip()) for x in thickness.split(",")])[:n]
    warning = ""
    if not ranked or ranked[0]["_score"] < RELIABLE_THRESHOLD:
        warning = "无可靠匹配，以下仅供参考"
    table = []
    for row in ranked:
        p = critical(row)
        table.append({"case_id": row["case_id"], "得分": round(row["_score"], 2), "可信度": row["_confidence"],
                      "热源": p["heat_source"], "焊缝区": p["weld_zone_mm"], "截面间距": p["section_spacing"],
                      "步长": p["time_step"], "约束": p["constraint"], "来源文件": row["source_files"]})
    return warning, table
