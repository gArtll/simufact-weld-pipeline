# -*- coding: utf-8 -*-
"""check_short_v3 regression set (B5 + C4c + tube_on_plate). Run BEFORE and AFTER every change to gate/check_short_v3.py.
Inputs: .dat copies under gate/regression_cases/ (or env WELDSIM_REGRESSION_CASES) and a tube-on-plate reference
directory (cases/tube_on_plate/reference/, kept outside the public repository). Missing inputs are skipped.
These .dat files are solver input with full meshes and are not in the repository; a case whose inputs are missing is
reported as skipped, not as consistent.
Usage: python run_regressions.py [--jobs 4] [--only NAME[,NAME]] [--out DIR]
Exit 0 = every case that ran is consistent with its expectation; 1 otherwise. Table: <out>/regression_table.md"""
import argparse
import concurrent.futures as cf
import io
import os
import re
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "check_short_v3.py")
RC = os.environ.get("WELDSIM_REGRESSION_CASES") or os.path.join(HERE, "regression_cases")


def dat(name):
    return os.path.join(RC, name, "case.dat")


def proc(name):
    return os.path.join(RC, name, "Proc")


REF, RP = dat("ref"), proc("ref")
TUBE_ROOT = os.path.join(os.path.dirname(HERE), "cases", "tube_on_plate", "reference")
TUBE_REF, TUBE_RP = os.path.join(TUBE_ROOT, "dat_ref.dat"), os.path.join(TUBE_ROOT, "Proc")
# name, source task, args, expected exit, expected FAIL set, expected ALLOW set (None = don't care), expected EQUIV set
CASES = [
    ("reg0_usage", "B3", [dat("scriptbead")], 2, set(), None, None),
    ("reg1_ref_vs_ref", "B3", [REF, REF], 0, set(), set(), {"ORIENT"}),
    ("reg2_scriptbead", "B3", [dat("scriptbead"), REF], 0, set(), set(), {"ORIENT"}),
    ("reg3a_20s_noallow", "B3", [dat("scriptbead_20s"), REF], 1, {"CONTACT_TABLE", "AUTO_STEP"}, set(), {"ORIENT"}),
    ("reg3b_20s_allow", "B3", [dat("scriptbead_20s"), REF, "--allow", "AUTO_STEP"], 0, set(), {"CONTACT_TABLE", "AUTO_STEP"}, {"ORIENT"}),
    ("reg4_localvec", "B3", [dat("localvec"), REF], 1, {"ORIENT", "NDSQ"}, set(), set()),
    ("reg5_A3_nopatch_280s", "B3/A3", [dat("A3_nopatch_280s"), REF], 1, {"ORIENT", "CONTACT_TABLE", "AUTO_STEP", "NDSQ"}, set(), set()),
    ("B4_reg1_ref_proc", "B4", [REF, REF, "--proc", RP, "--proc-ref", RP], 0, set(), set(), {"ORIENT"}),
    ("B4_reg2_A3_nopatch_proc", "B4", [dat("A3_nopatch_280s"), REF, "--proc", proc("A3_nopatch_280s"), "--proc-ref", RP], 1,
     {"ORIENT", "CONTACT_TABLE", "AUTO_STEP", "NDSQ", "PROC"}, set(), set()),
    ("orient_reg1_ref", "A4b", [REF, REF, "--proc", RP, "--proc-ref", RP], 0, set(), set(), {"ORIENT"}),
    ("orient_reg2_A4_280s_nocontact", "A4b", [dat("A4_280s"), REF, "--proc", proc("A4_280s"), "--proc-ref", RP], 1, {"CONTACT_TABLE"}, set(), {"ORIENT"}),
    ("orient_reg3_localvec", "A4b", [dat("localvec"), REF], 1, {"ORIENT", "NDSQ"}, set(), set()),
    ("reg3b_A4b_280s_v2_proc", "B4b", [dat("A4b_280s_v2"), REF, "--proc", proc("A4b_280s_v2"), "--proc-ref", RP], 0, set(), set(), {"ORIENT"}),
    ("C4b_indep_A4b_280s_v2", "C4b", [dat("A4b_280s_v2"), REF, "--proc", proc("A4b_280s_v2"), "--proc-ref", RP, "--independent-bead"], 0,
     set(), set(), {"ORIENT"}),
    ("C4b_indep_C4_280s", "C4b/C4c", [dat("C4_280s"), REF, "--proc", proc("C4_280s"), "--proc-ref", RP, "--independent-bead"], 1,
     {"ORIENT", "CONTACT_TABLE", "AUTO_STEP", "BEAD_DIST"}, set(), set()),
    ("C4c_indep_280s", "C4c", [dat("C4c_280s"), REF, "--proc", proc("C4c_280s"), "--proc-ref", RP, "--independent-bead"], 0, set(), set(), {"ORIENT"}),
    ("tube_on_plate_ref_vs_ref", "E4", [TUBE_REF, TUBE_REF, "--proc", TUBE_RP, "--proc-ref", TUBE_RP, "--independent-bead"], 0, set(), set(), {"ORIENT"}),
]
LABEL = {"通过": "PASS", "失败": "FAIL", "白名单放行": "ALLOW", "数值等价": "EQUIV"}


def parse(text):
    items, on = {}, False
    for line in text.splitlines():
        if line.startswith("== 结论"):
            on = True
            continue
        if on:
            m = re.match(r"^\s{3}(\w+)\s+(通过|失败|白名单放行|数值等价)\s*$", line)
            if m:
                items[m.group(1)] = LABEL[m.group(2)]
    return items


def fmt(s):
    return ",".join(sorted(s)) if s else "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=os.path.join(HERE, "regression_out"))
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, "out"), exist_ok=True)
    only = set(filter(None, a.only.split(",")))
    cases = [c for c in CASES if not only or c[0] in only]

    def missing(args):
        return [x for x in args if (x.endswith(".dat") or x.endswith("Proc")) and not os.path.exists(x)]

    def run(case):
        name, _, args, *_ = case
        if missing(args):
            return name, None, {}, 0.0
        extra = [] if name == "reg0_usage" else ["--work", os.path.join(a.out, "work", name)]
        t0 = time.time()
        r = subprocess.run([sys.executable, GATE] + args + extra, capture_output=True, text=True, encoding="utf-8", errors="replace")
        io.open(os.path.join(a.out, "out", name + ".txt"), "w", encoding="utf-8").write(
            "CMD check_short_v3.py %s\n%s%s\nexit=%d\n" % (" ".join(args + extra), r.stdout, r.stderr, r.returncode))
        return name, r.returncode, parse(r.stdout), time.time() - t0

    with cf.ThreadPoolExecutor(a.jobs) as ex:
        res = {n: (rc, it, dt) for n, rc, it, dt in ex.map(run, cases)}
    rows, bad, skipped = [], 0, 0
    for name, src, args, e_exit, e_fail, e_allow, e_equiv in cases:
        rc, it, dt = res[name]
        if rc is None:
            skipped += 1
            rows.append("| %s | %s | exit %d | 输入缺失：%s | 跳过 | — |" % (name, src, e_exit, ", ".join(os.path.relpath(x, os.path.dirname(HERE)) for x in missing(args))))
            continue
        a_fail = {k for k, v in it.items() if v == "FAIL"}
        a_allow = {k for k, v in it.items() if v == "ALLOW"}
        a_equiv = {k for k, v in it.items() if v == "EQUIV"}
        ok = rc == e_exit and a_fail == e_fail and (e_allow is None or a_allow == e_allow) and (e_equiv is None or a_equiv == e_equiv)
        bad += not ok
        rows.append("| %s | %s | exit %d；失败 %s；放行 %s；数值等价 %s | exit %d；失败 %s；放行 %s；数值等价 %s | %s | %.0f s |" % (
            name, src, e_exit, fmt(e_fail), "—" if e_allow is None else fmt(e_allow), "—" if e_equiv is None else fmt(e_equiv),
            rc, fmt(a_fail), fmt(a_allow), fmt(a_equiv), "是" if ok else "**否**", dt))
    table = ("# check_short_v3 回归表  %s\n\n| 用例 | 来源 | 预期 | 实际 | 是否一致 | 耗时 |\n|---|---|---|---|---|---|\n%s\n\n合计 %d 例，一致 %d，不一致 %d，跳过 %d\n"
             % (time.strftime("%Y-%m-%d %H:%M:%S"), "\n".join(rows), len(cases), len(cases) - bad - skipped, bad, skipped))
    io.open(os.path.join(a.out, "regression_table.md"), "w", encoding="utf-8").write(table)
    print(table)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
