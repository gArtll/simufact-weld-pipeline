# -*- coding: utf-8 -*-
r"""Short-model .dat gate v3 (read-only, R11 / R15 / R20 / R23). Copied from P0_B3 check_short_v3.py (state after
B4, B4b, A4b, C4b edits). Package change only: .dat parsing and Qt XML decoding come from common/, helper scripts are
found next to this file. Judgement logic unchanged; gate/run_regressions.py must stay at exit 0.

Usage: python check_short_v3.py <new.dat> <ref.dat> [--allow ITEM ...] [--work DIR] [--orient-tol 2e-3]
                                [--proc <Proc dir of new> --proc-ref <Proc dir of reference>] [--independent-bead]
  ORIENT is a numeric-tolerance item: pass is reported as "数值等价（最大差 …）" (arc length <= 1e-5 m, component <= 2e-3).
  --independent-bead (R20): NDSQ threshold 0.005 mm; BEAD_DIST = 10 um sub-band <= ref AND full band <= ref*1.1.
  PROC compares connect_to_nodes, calculation, orientation_mode, variation_angle, variation_offset and
       reference_volume/radius (1e-9 m).
  ITEM in: ORIENT CONTACT_TABLE AUTO_STEP NDSQ BEAD_SET BEAD_DIST DETJ PROC
Exit: 0 pass (or pass with whitelist / numeric equivalence), 1 fail, 2 usage error.
"""
import io
import json
import math
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common import dat as D  # noqa: E402
from common.qxml import robot_blobs as _robot_blobs  # noqa: E402

ITEMS = ("ORIENT", "CONTACT_TABLE", "AUTO_STEP", "NDSQ", "BEAD_SET", "BEAD_DIST", "DETJ", "PROC")
BEAD_NODES = os.path.join(HERE, "check_bead_nodes.py")
DETJ = os.path.join(HERE, "check_detj.py")


def usage(msg=""):
    if msg:
        print("错误: " + msg)
    print(__doc__)
    sys.exit(2)


args = sys.argv[1:]
allow, work, pos = set(), None, []
proc_new = proc_ref = None
ORIENT_TOL = 2e-3
ORIENT_ARC_TOL = 1e-5
PROC_RADIUS_TOL = 1e-9
INDEPENDENT = False
NDSQ_TOL = 1e-3
i = 0
while i < len(args):
    a = args[i]
    if a == "--allow":
        if i + 1 >= len(args):
            usage("--allow 缺参数")
        for v in args[i + 1].split(","):
            if v.upper() not in ITEMS:
                usage("未知白名单项 %s" % v)
            allow.add(v.upper())
        i += 2
    elif a == "--work":
        if i + 1 >= len(args):
            usage("--work 缺参数")
        work = args[i + 1]
        i += 2
    elif a == "--independent-bead":
        INDEPENDENT = True
        NDSQ_TOL = 5e-3
        i += 1
    elif a == "--orient-tol":
        if i + 1 >= len(args):
            usage("--orient-tol 缺参数")
        try:
            ORIENT_TOL = float(args[i + 1])
        except ValueError:
            usage("--orient-tol 不是数字: %s" % args[i + 1])
        i += 2
    elif a in ("--proc", "--proc-ref"):
        if i + 1 >= len(args):
            usage("%s 缺参数" % a)
        if a == "--proc":
            proc_new = args[i + 1]
        else:
            proc_ref = args[i + 1]
        i += 2
    elif a.startswith("-"):
        usage("未知参数 %s" % a)
    else:
        pos.append(a)
        i += 1
if len(pos) != 2:
    usage("必须给出 <new.dat> <ref.dat>")
NEW, REF = pos
for p in (NEW, REF):
    if not os.path.isfile(p):
        usage("文件不存在 %s" % p)
if (proc_new is None) != (proc_ref is None):
    usage("--proc 与 --proc-ref 必须同时给出")
for p in (proc_new, proc_ref):
    if p is not None and not os.path.isfile(os.path.join(p, "robots_properties.xml")):
        usage("Proc 目录下没有 robots_properties.xml: %s" % p)
if work is None:
    work = os.path.join(HERE, "work_" + os.path.splitext(os.path.basename(NEW))[0] + "__vs__" + os.path.splitext(os.path.basename(REF))[0])
os.makedirs(work, exist_ok=True)

L = {"new": D.load(NEW), "ref": D.load(REF)}
results = []   # (item, status, detail) status in PASS FAIL ALLOW EQUIV


def P(s=""):
    print(s, flush=True)


def verdict(item, ok, detail):
    st = "PASS" if ok else ("ALLOW" if item in allow else "FAIL")
    results.append((item, st, detail))
    P("   [%s] %s: %s" % ({"PASS": "通过", "FAIL": "失败", "ALLOW": "白名单放行"}[st], item, detail))


def first_diff(a, b):
    for j, (p, q) in enumerate(zip(a, b)):
        if p != q:
            return "第 %d 行\n        新: %s\n        参: %s" % (j, p.rstrip()[:140], q.rstrip()[:140])
    return "行数 %d / 参照 %d" % (len(a), len(b))


nocomment = D.nocomment

P("新: %s  (%d 行)" % (NEW, len(L["new"])))
P("参: %s  (%d 行)" % (REF, len(L["ref"])))
P("白名单: %s" % (sorted(allow) or "无"))

# ---- ORIENT (numeric tolerance)
P("\n== ORIENT 方向表（数值容差：弧长 ≤ %.0e m，方向分量 ≤ %.0e）" % (ORIENT_ARC_TOL, ORIENT_TOL))
t = {k: [nocomment(b) for b in D.tables(L[k], "table_nodal_connection")] for k in L}
ok = len(t["new"]) == len(t["ref"])
det = "新 %d 张 / 参照 %d 张" % (len(t["new"]), len(t["ref"]))
max_arc = max_cmp = 0.0
n_exact = 0
for n, (a, b) in enumerate(zip(t["new"], t["ref"]), 1):
    if a[:3] != b[:3]:
        ok = False
        det += "；第 %d 张表头不同 %s" % (n, first_diff(a[:3], b[:3]))
        continue
    if len(a) != len(b):
        ok = False
        det += "；第 %d 张行数 %d / 参照 %d" % (n, len(a) - 3, len(b) - 3)
        continue
    ta, tb = a[3:], b[3:]
    try:
        rows = [(D.fixed20(p), D.fixed20(q)) for p, q in zip(ta, tb)]
    except ValueError as exc:
        ok = False
        det += "；第 %d 张数值解析失败 %r" % (n, exc)
        continue
    if any(len(u) != 2 or len(v) != 2 for u, v in rows):
        ok = False
        det += "；第 %d 张存在非两列数据行" % n
        continue
    da = max(abs(u[0] - v[0]) for u, v in rows)
    dc = max(abs(u[1] - v[1]) for u, v in rows)
    n_exact += int(ta == tb)
    P("   第 %d 张 %-40s 行 %d，最大弧长差 %.3e m，最大方向分量差 %.3e%s" % (n, a[0].strip()[:40], len(ta), da, dc, "（逐字相同）" if ta == tb else ""))
    max_arc, max_cmp = max(max_arc, da), max(max_cmp, dc)
    if da > ORIENT_ARC_TOL or dc > ORIENT_TOL:
        ok = False
        det += "；第 %d 张超容差（弧长 %.3e m，方向分量 %.3e）" % (n, da, dc)
det += "；逐字相同 %d 张；最大弧长差 %.3e m，最大方向分量差 %.3e" % (n_exact, max_arc, max_cmp)
if ok:
    results.append(("ORIENT", "EQUIV", det))
    P("   [数值等价] ORIENT: 数值等价（最大差：弧长 %.3e m，方向分量 %.3e）；%s" % (max_arc, max_cmp, det))
else:
    verdict("ORIENT", ok, det)

# ---- CONTACT TABLE
P("\n== CONTACT_TABLE")
s = {k: D.section(L[k], "CONTACT TABLE") for k in L}
same_content = len(s["new"]) > 0 and len(s["ref"]) > 0 and all(nocomment(a) == nocomment(b) for a, b in zip(s["new"], s["ref"]))
ok = same_content and len(s["new"]) == len(s["ref"])
det = "块数 新 %d / 参照 %d，逐块内容相同=%s" % (len(s["new"]), len(s["ref"]), same_content)
for n, (a, b) in enumerate(zip(s["new"], s["ref"])):
    if nocomment(a) != nocomment(b):
        det += "；第 %d 块不同 %s" % (n + 1, first_diff(nocomment(a), nocomment(b)))
        break
ncom = sum(1 for a, b in zip(s["new"], s["ref"]) for p, q in zip(a, b) if p != q and p.startswith("$"))
if ncom:
    P("   (信息) $ 注释行不同 %d 行，不计入判定" % ncom)
if not ok and same_content and "AUTO_STEP" in allow and "CONTACT_TABLE" not in allow:
    # one CONTACT TABLE block per load case: a pure block-count difference follows the AUTO STEP (end time) change
    results.append(("CONTACT_TABLE", "ALLOW", det + "；仅块数不同（随工况数），随 AUTO_STEP 白名单放行"))
    P("   [白名单放行] CONTACT_TABLE: %s；仅块数不同（随工况数），随 AUTO_STEP 白名单放行" % det)
else:
    verdict("CONTACT_TABLE", ok, det)

# ---- AUTO STEP
P("\n== AUTO_STEP")
s = {k: D.section(L[k], "AUTO STEP") for k in L}
ok = len(s["new"]) == len(s["ref"]) and len(s["ref"]) > 0 and all(nocomment(a) == nocomment(b) for a, b in zip(s["new"], s["ref"]))
det = "块数 新 %d / 参照 %d" % (len(s["new"]), len(s["ref"]))
for n, (a, b) in enumerate(zip(s["new"], s["ref"])):
    if nocomment(a) != nocomment(b):
        det += "；第 %d 块不同 %s" % (n + 1, first_diff(nocomment(a), nocomment(b)))
        break
verdict("AUTO_STEP", ok, det)

# ---- NDSQ
P("\n== NDSQ 热源路径")
C = {k: D.coords(L[k]) for k in L}
q = {k: D.ndsq_sets(L[k]) for k in L}
for k in L:
    P("   %s: %s" % (k, [(n, len(ids)) for n, ids in q[k]]))
ok = len(q["new"]) == len(q["ref"])
parts = ["集合数 新 %d / 参照 %d" % (len(q["new"]), len(q["ref"]))]
if ok:
    for (n1, a), (n2, b) in zip(q["new"], q["ref"]):
        if len(a) != len(b):
            ok = False
            parts.append("%s 点数 %d / 参照 %d" % (n1, len(a), len(b)))
            continue
        missing = [i for i in a if i not in C["new"]] + [j for j in b if j not in C["ref"]]
        if missing:
            ok = False
            parts.append("%s 有 %d 个节点无坐标" % (n1, len(missing)))
            continue
        dev = max(sum((x - y) ** 2 for x, y in zip(C["new"][i], C["ref"][j])) ** 0.5 for i, j in zip(a, b))
        parts.append("%s %d 点，节点号相同=%s，最大位置差 %.6f mm" % (n1, len(a), a == b, dev))
        if not dev < NDSQ_TOL:
            ok = False
parts.append("阈值 %.3f mm%s" % (NDSQ_TOL, "（--independent-bead）" if INDEPENDENT else ""))
if not q["ref"]:
    parts.append("双方均为 connect_to_nodes=false，无 NDSQ 集合")
verdict("NDSQ", ok, "；".join(parts))

# ---- BEAD SET
P("\n== BEAD_SET 焊道单元集合")
fs = {k: [(n, D.element_set(L[k], n)) for n in D.weld_fill_sets(L[k])] for k in L}
for k in L:
    P("   %s: %s" % (k, [(n, len(ids)) for n, ids in fs[k]]))
ok = len(fs["ref"]) > 0 and len(fs["new"]) == len(fs["ref"]) and all(len(a) == len(b) and len(a) > 0 for (_, a), (_, b) in zip(fs["new"], fs["ref"]))
verdict("BEAD_SET", ok, "新 %s / 参照 %s" % ([len(a) for _, a in fs["new"]], [len(b) for _, b in fs["ref"]]))

# ---- PROC
PROC_FIELDS = ("connect_to_nodes", "calculation", "orientation_mode", "variation_angle", "variation_offset")


def robot_blobs(proc_dir):
    return _robot_blobs(os.path.join(proc_dir, "robots_properties.xml"))


def traj_fields(xml):
    root = ET.fromstring(xml.strip().encode("utf-8"))
    rob = root.find("name").get("display_name") if root.find("name") is not None else "?"
    res = []
    for tr_ in root.findall("./weld_sequence/trajectory"):
        tm = tr_.find("trajectory_modification")
        if tm is None:
            res.append((rob, tr_.get("display_name"), None))
            continue
        f = {}
        for tag in ("connect_to_nodes", "calculation", "orientation_mode"):
            e = tm.find(tag)
            f[tag] = None if e is None else (e.text or "").strip()
        for tag in ("variation_angle", "variation_offset"):
            e = tm.find("modification/" + tag)
            if e is None:
                f[tag] = None
            else:
                v = float(e.get("value"))
                if tag == "variation_angle" and e.get("unit") == "1":      # unit code 1 = degree, 0 = rad
                    v = math.radians(v)
                f[tag] = (round(v, 12), e.get("dimension"), e.get("unit") if tag == "variation_offset" else "rad")
        e = tm.find("reference_volume/radius")
        f["reference_volume_radius"] = None if e is None else (float(e.get("value")), e.get("dimension"), e.get("unit"))
        res.append((rob, tr_.get("display_name"), f))
    return res


if proc_new is not None:
    P("\n== PROC 轨迹设置（robots_properties.xml）")
    P("   新: %s" % proc_new)
    P("   参: %s" % proc_ref)
    try:
        tn = [x for b in robot_blobs(proc_new) for x in traj_fields(b)]
        tr = [x for b in robot_blobs(proc_ref) for x in traj_fields(b)]
        ok = len(tn) == len(tr) and len(tr) > 0
        parts = ["轨迹数 新 %d / 参照 %d" % (len(tn), len(tr))]
        for (rn, nn, fn), (rr, nr, fr) in zip(tn, tr):
            if fn is None or fr is None:
                ok = False
                parts.append("%s/%s 缺 trajectory_modification 块（新 %s / 参照 %s）" % (rn, nn, "缺" if fn is None else "有", "缺" if fr is None else "有"))
                continue
            for tag in PROC_FIELDS + ("reference_volume_radius",):
                if tag == "reference_volume_radius":
                    a_, b_ = fn[tag], fr[tag]
                    same = a_ is not None and b_ is not None and a_[1:] == b_[1:] and abs(a_[0] - b_[0]) <= PROC_RADIUS_TOL
                else:
                    same = fn[tag] == fr[tag] and fn[tag] is not None
                P("   %-10s %-18s 新 %-28s 参 %-28s %s" % (nn, tag, fn[tag], fr[tag], "相同" if same else "不同"))
                if not same:
                    ok = False
                    parts.append("%s(参照 %s) %s 新 %s / 参照 %s" % (nn, nr, tag, fn[tag], fr[tag]))
        verdict("PROC", ok, "；".join(parts))
    except Exception as exc:
        verdict("PROC", False, "检查执行失败: %r" % exc)
else:
    P("\n== PROC 未指定 --proc/--proc-ref，跳过（不计入判定）")


# ---- extract meshes for BEAD_DIST / DETJ
def export(k):
    conn = D.connectivity(L[k])
    xyz = C[k]
    bead_elems = set()
    files = []
    for idx, (name, ids) in enumerate(fs[k]):
        bead_elems.update(ids)
        path = os.path.join(work, "%s_bead%d.bdf" % (k, idx + 1))
        nodes = sorted({n for e in ids for n in conn[e]})
        with io.open(path, "w", encoding="ascii", newline="\n") as f:
            f.write("$ bead set %s extracted from %s (mm)\nBEGIN BULK\n" % (name, NEW if k == "new" else REF))
            for n in nodes:
                x, y, z = xyz[n]
                f.write("GRID*   %16d%16s%16.9e%16.9e*\n*       %16.9e\n" % (n, "", x, y, z))
            for e in ids:
                c = conn[e]
                f.write("CHEXA   %8d%8d%s+\n+       %8d%8d\n" % (e, 1, "".join("%8d" % v for v in c[:6]), c[6], c[7]))
            f.write("ENDDATA\n")
        files.append(path)
    mother = sorted({n for e, c in conn.items() if e not in bead_elems for n in c})
    mpath = os.path.join(work, "%s_mother_nodes.bdf" % k)
    with io.open(mpath, "w", encoding="ascii", newline="\n") as f:
        f.write("$ non-bead nodes extracted from %s (mm)\nBEGIN BULK\n" % (NEW if k == "new" else REF))
        for n in mother:
            x, y, z = xyz[n]
            f.write("GRID*   %16d%16s%16.9e%16.9e*\n*       %16.9e\n" % (n, "", x, y, z))
        f.write("ENDDATA\n")
    return files, mpath


# check_bead_nodes' contact-boundary classification assumes bead-local numbering from 1; beads extracted from the .dat
# carry GLOBAL node numbers, so that classification is invalid here. The gate uses only all_nodes and the histogram.
P("\n== BEAD_DIST 焊道距离分布（check_bead_nodes.py）")
danger = {}
try:
    import csv as _csv
    for k in L:
        beads, mother = export(k)
        od = os.path.join(work, "bead_nodes_" + k)
        cmd = [sys.executable, BEAD_NODES, "--web", mother, "--plate", mother, "--out-dir", od]
        for b in beads:
            cmd += ["--bead", b]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        js = os.path.join(od, "bead_node_check.json")
        if not os.path.exists(js):
            raise RuntimeError("check_bead_nodes.py 无输出 (exit %d): %s" % (r.returncode, r.stderr[-400:]))
        bj = json.load(io.open(js, encoding="utf-8"))["beads"]
        danger[k] = [b["all_nodes"]["danger_1e-6_to_0.1_mm"] for b in bj]
        sub = {}
        for row in _csv.DictReader(io.open(os.path.join(od, "bead_node_histogram.csv"), encoding="utf-8-sig")):
            if row["bin_mm"] in ("[1e-06,0.0001)", "[0.0001,0.001)", "[0.001,0.01)"):
                sub[row["bead"]] = sub.get(row["bead"], 0) + int(row["count"])
        danger[k + "_10um"] = [sub.get(os.path.basename(b["file"]), 0) for b in bj]
        P("   %s 危险区计数(1e-6~0.1 mm) 每条焊道: %s；10 µm 子带(1e-6~0.01 mm): %s   [%s]" % (k, danger[k], danger[k + "_10um"], js))
    if INDEPENDENT:
        n_ok = len(danger["new"]) == len(danger["ref"]) > 0
        sub_ok = n_ok and all(a <= b for a, b in zip(danger["new_10um"], danger["ref_10um"]))
        full_ok = n_ok and all(a <= b * 1.1 for a, b in zip(danger["new"], danger["ref"]))
        verdict("BEAD_DIST", sub_ok and full_ok, "（--independent-bead 两层）10 µm 子带 新 %s ≤ 参照 %s：%s；全带 新 %s ≤ 参照×1.1 %s：%s" % (
            danger["new_10um"], danger["ref_10um"], "是" if sub_ok else "否", danger["new"],
            [round(b * 1.1, 1) for b in danger["ref"]], "是" if full_ok else "否"))
    else:
        ok = len(danger["new"]) == len(danger["ref"]) > 0 and all(a <= b for a, b in zip(danger["new"], danger["ref"]))
        verdict("BEAD_DIST", ok, "新 %s ≤ 参照 %s" % (danger["new"], danger["ref"]))
except Exception as exc:
    verdict("BEAD_DIST", False, "检查执行失败: %r" % exc)

P("\n== DETJ（check_detj.py）")
try:
    beads = sorted(p for p in os.listdir(work) if p.startswith("new_bead") and p.endswith(".bdf"))
    js = os.path.join(work, "detj_new.json")
    r = subprocess.run([sys.executable, DETJ] + [os.path.join(work, b) for b in beads] + ["--json", js],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in r.stdout.strip().splitlines():
        P("   " + line)
    if r.returncode != 0 or not os.path.exists(js):
        raise RuntimeError("check_detj.py exit %d: %s" % (r.returncode, r.stderr[-400:]))
    dj = json.load(io.open(js, encoding="utf-8"))
    nonpos = [v["nonpositive"] for v in dj.values()]
    verdict("DETJ", len(nonpos) > 0 and all(v == 0 for v in nonpos), "非正 Gauss 点 %s" % nonpos)
except Exception as exc:
    verdict("DETJ", False, "检查执行失败: %r" % exc)

# ---- summary
fails = [r for r in results if r[1] == "FAIL"]
allowed = [r for r in results if r[1] == "ALLOW"]
equiv = [r for r in results if r[1] == "EQUIV"]
P("\n== 结论")
for item, st, _ in results:
    P("   %-14s %s" % (item, {"PASS": "通过", "FAIL": "失败", "ALLOW": "白名单放行", "EQUIV": "数值等价"}[st]))
for item in sorted(allow - {r[0] for r in allowed}):
    P("   (提示) 白名单项 %s 未被使用" % item)
if fails:
    P("结论: 失败（%s）  exit 1" % ", ".join(r[0] for r in fails))
    sys.exit(1)
eq_note = "；数值等价: %s" % ", ".join(r[0] for r in equiv) if equiv else ""
if allowed:
    P("结论: 白名单放行（%s%s）  exit 0" % (", ".join(r[0] for r in allowed), eq_note))
    sys.exit(0)
P("结论: 通过%s  exit 0" % ("（%s）" % eq_note.lstrip("；") if equiv else ""))
sys.exit(0)
