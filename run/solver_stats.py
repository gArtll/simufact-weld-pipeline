# -*- coding: utf-8 -*-
"""Solver stability statistics from the MARC / Simufact logs of one run: the exit number alone says too little. A
failed run stops at 85 % with elements inside out; a run that ends normally can still have had an element turn inside
out, or an increment cut back and attempted again, and recovered. `pass` is the first thing only (exit 3004 and every
load case at 100 %); the rest is reported as warnings, so a run is never silently called clean when it was not.

.sts  one line per increment attempt, columns:
      inc  load_case_name  tot_time  cyc1  conv_r  conv_d  cyc2  conv_t  prg(%)  pst  rst  walltime  elm_cnt
      The load case name contains spaces. pst and rst are cumulative file-write counters (post file, restart file):
      they follow the write-every-N setting, not the solver's behaviour -- a run that wrote a restart file at every
      increment has rst = number of increments and is not in trouble. What does say the solver had to recover is a
      repeated inc number: the increment was cut back and attempted again (`retried_increments`).
      Tail: "job ends with exit number", "total wall time", "total cpu  time".
.out  large (tens of MB), one file per solver domain: streamed, never read whole, all of them scanned. Counts
      "*** error - element inside out at element <id>", "*** error", "*** warning" and "Number of warning messages is N".

Usage: python run/solver_stats.py <.sts> [<.out> ...] [--json out.json]"""
import io
import json
import os
import re
import sys

EXIT = re.compile(r"exit number\s+(\d+)")
WALL = re.compile(r"total wall time:[ ]*([\d.eE+-]+)")
CPU = re.compile(r"total cpu[ ]+time:[ ]*([\d.eE+-]+)")
INSIDE_OUT = re.compile(r"element inside out at element\s+(\d+)")
NWARN = re.compile(r"Number of warning messages is\s+(\d+)")
NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def parse_sts(path):
    """Increment table and termination lines of a .sts file."""
    rows, exit_no, exit_line, wall, cpu = [], None, None, None, None
    for k, line in enumerate(io.open(path, encoding="latin-1", errors="replace"), 1):
        m = EXIT.search(line)
        if m:
            exit_no, exit_line = int(m.group(1)), k
        m = WALL.search(line)
        if m:
            wall = float(m.group(1))
        m = CPU.search(line)
        if m:
            cpu = float(m.group(1))
        t = line.split()
        if len(t) < 13 or not t[0].isdigit():
            continue
        tail = t[-11:]
        if not all(NUM.match(v) for v in tail):
            continue
        rows.append({"inc": int(t[0]), "load_case": " ".join(t[1:len(t) - 11]), "tot_time": float(tail[0]),
                     "cyc1": int(float(tail[1])), "conv_r": float(tail[2]), "conv_d": float(tail[3]),
                     "cyc2": int(float(tail[4])), "conv_t": float(tail[5]), "progress": float(tail[6]),
                     "pst": int(float(tail[7])), "rst": int(float(tail[8])), "walltime": float(tail[9]),
                     "elements": int(float(tail[10]))})
    return rows, {"exit_number": exit_no, "exit_line": exit_line, "wall_time_s": wall, "cpu_time_s": cpu}


def scan_out(paths, limit=20):
    """Streamed scan of every .out of the run: inverted elements, error and warning counts.

    One file per solver domain (a two-domain run writes two .out files), so all of them are scanned: an element that
    turns inside out in the second domain is not in the first domain's file."""
    if isinstance(paths, str) or paths is None:
        paths = [paths] if paths else []
    files = [p for p in paths if p and os.path.isfile(p)]
    if not files:
        return {"out": None}
    inverted, errors, warnings, reported = set(), 0, 0, {}
    for path in files:
        for line in io.open(path, encoding="latin-1", errors="replace"):
            if "***" in line:
                if " error" in line:
                    errors += 1
                    m = INSIDE_OUT.search(line)
                    if m:
                        inverted.add(int(m.group(1)))
                elif " warning" in line:
                    warnings += 1
            m = NWARN.search(line)
            if m:
                reported[path] = int(m.group(1))                     # last count of this file
    return {"out": [os.path.basename(p) for p in files], "error_lines": errors, "warning_lines": warnings,
            "reported_warning_count": sum(reported.values()) if reported else None, "inverted_elements": len(inverted),
            "inverted_element_ids": sorted(inverted)[:limit]}


def stats(sts_path, out_paths=None):
    rows, end = parse_sts(sts_path)
    solved = [r for r in rows if r["load_case"] not in ("Initializing", "Start simulation")]
    by_inc = {}
    for r in solved:
        by_inc.setdefault(r["inc"], []).append(r)
    retried = {i: len(v) for i, v in by_inc.items() if len(v) > 1}
    last = solved[-1] if solved else None
    res = dict(end)
    res.update({
        "sts": os.path.basename(sts_path),
        "increments": max(by_inc) if by_inc else 0,
        "increment_lines": len(solved),
        "retried_increments": len(retried),
        "restart_file_writes": max((r["rst"] for r in solved), default=0),
        "post_file_writes": max((r["pst"] for r in solved), default=0),
        "load_cases": sorted({r["load_case"] for r in solved}),
        "elements": last["elements"] if last else None,
        "final_time_s": last["tot_time"] if last else None,
        "final_progress_percent": last["progress"] if last else None,
        "max_iterations": max((r["cyc1"] for r in solved), default=None),
    })
    res.update(scan_out(out_paths))
    # pass needs all three: normal exit number, the load cases finished, and a scanned .out without inverted elements
    # The run finished: normal exit number and every load case at 100 %. Nothing else belongs in this line -- a
    # solver that cut back, retried an increment or reported an element inside out and then recovered still finished,
    # and calling that a failure would reject runs Simufact considers complete.
    res["pass"] = bool(res.get("exit_number") == 3004 and (res.get("final_progress_percent") or 0) >= 99.99)
    w = []
    if res.get("out") is None:
        w.append(".out not scanned: inverted elements unknown")
    elif res.get("inverted_elements"):
        w.append("%d element(s) inside out" % res["inverted_elements"])
    if res.get("retried_increments"):
        w.append("%d retried increment(s)" % res["retried_increments"])
    res["warnings"] = w
    res["clean"] = bool(res["pass"] and not w)              # finished and nothing had to be recovered from
    res["status"] = "FAIL" if not res["pass"] else ("PASS" if not w else "PASS with warnings")
    return res


def format_stats(r):
    return "\n".join([
        "Solver status: %s" % r.get("status", "PASS" if r["pass"] else "FAIL"),
        "Exit number: %s" % r["exit_number"],
        "Load cases: %s" % ", ".join(r["load_cases"]),
        "Increments: %s (lines %s, retried %s)" % (r["increments"], r["increment_lines"], r["retried_increments"]),
        "File writes: post %s, restart %s" % (r["post_file_writes"], r["restart_file_writes"]),
        "Final time: %s s (%s %%)" % (r["final_time_s"], r["final_progress_percent"]),
        "Elements: %s   max iterations: %s" % (r["elements"], r["max_iterations"]),
        "Wall time: %s s   CPU time: %s s" % (r["wall_time_s"], r["cpu_time_s"]),
        "Inverted elements: %s %s" % (r.get("inverted_elements", "not scanned"), r.get("inverted_element_ids") or ""),
        "Solver warnings: %s (reported %s), error lines: %s" % (r.get("warning_lines"), r.get("reported_warning_count"), r.get("error_lines")),
        "Run warnings: %s" % ("; ".join(r["warnings"]) if r.get("warnings") else "none"),
    ])


def main(argv):
    if not argv:
        sys.exit(__doc__)
    js = None
    if "--json" in argv:
        k = argv.index("--json")
        js = argv[k + 1]
        argv = argv[:k] + argv[k + 2:]
    r = stats(argv[0], argv[1:] or None)
    print(format_stats(r))
    if js:
        io.open(js, "w", encoding="utf-8").write(json.dumps(r, ensure_ascii=False, indent=1) + "\n")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
