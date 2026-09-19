# -*- coding: utf-8 -*-
"""Write the synthetic solver-log fixtures of this directory: two FAIL runs `synthetic_exit3015_{a,b}`, a PASS run
`synthetic_pass` and a PASS-with-warnings run `synthetic_pass_with_warnings` (.sts and .out each).

They are not excerpts of any solver run. Every number in them is generated here from a fixed seed and the parameters
below; they reproduce the *shape* of the failure `run/solver_stats.py` must recognise -- the same line formats, the
same verdicts -- and nothing of any real model. The FAIL runs show:

* the time step cut down to its minimum before the job stops with exit 3015, below 100 % progress;
* elements inside out in adjacent groups (a local collapse), one element reported many times;
* separation / recycling warnings dominating the warning list;
* the Newton iteration count rising at the end; in `b`, increments cut back and attempted again.

Usage: python tests/failure_logs/make_synthetic.py   (rewrites all eight files; the tests check what they contain)"""
import io
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))

CASES = {
    "a": {"seed": 11, "rows": 120, "elements": 12000, "p0": 58.20, "dp": 0.035, "wall0": 9000.0,
          "groups": [(401, 404), (2210, 2213), (9870, 9871)], "repeat": (9870, 12), "retried": [],
          "cyc_end": [9, 14, 22, 37, 88], "warn_total": 640, "wall": 12510.25, "cpu": 61880.40},
    "b": {"seed": 23, "rows": 140, "elements": 15500, "p0": 44.10, "dp": 0.040, "wall0": 7000.0,
          "groups": [(733, 735), (5120, 5124), (14002, 14003)], "repeat": (5122, 9), "retried": [31, 77, 78, 102, 120],
          "cyc_end": [8, 12, 19, 41, 96], "warn_total": 910, "wall": 11890.75, "cpu": 58420.10},
    # finished runs: exit 3004 at 100 %; the second recovered from one element inside out and one cut-back increment
    "pass": {"seed": 31, "rows": 110, "elements": 9000, "p0": 97.00, "final": 100.0, "wall0": 4000.0, "exit": 3004,
             "groups": [], "repeat": None, "retried": [], "cyc_end": [], "warn_total": 120, "wall": 6120.40,
             "cpu": 30110.80, "time_step_error": False},
    "pass_warn": {"seed": 37, "rows": 130, "elements": 16000, "p0": 96.50, "final": 100.0, "wall0": 9000.0,
                  "exit": 3004, "groups": [(12011, 12011)], "repeat": (12011, 1), "retried": [64], "cyc_end": [],
                  "warn_total": 410, "wall": 14880.10, "cpu": 88020.55, "time_step_error": False},
}
FILES = {"a": "synthetic_exit3015_a", "b": "synthetic_exit3015_b", "pass": "synthetic_pass",
         "pass_warn": "synthetic_pass_with_warnings"}
HEADER_STS = ("# SYNTHETIC fixture written by make_synthetic.py -- not from any solver run. Same line format as a\n"
              "# Simufact/MARC .sts increment table; every value generated. Simulated time and file counters are 0.\n\n"
              "Simufact Solver (synthetic fixture)\n\n"
              "  inc     load_case_name   tot_time  cyc1  conv_r    conv_d  cyc2  conv_t   prg(%)   pst   rst  walltime  elm_cnt\n"
              "-----------------------------------------------------------------------------------------------------------------\n")


def sts(c):
    rnd = random.Random(c["seed"])
    lines, inc, wall, prog = [], 0, c["wall0"], c["p0"]
    dp = c["dp"] if "dp" in c else (c["final"] - c["p0"]) / (c["rows"] - len(c["retried"]))
    for k in range(c["rows"]):
        if k not in c["retried"]:
            inc += 1
            prog += dp
        cyc = rnd.randint(5, 8)
        tail = c["rows"] - k
        if tail <= len(c["cyc_end"]):
            cyc = c["cyc_end"][-tail]
        wall += rnd.uniform(15.0, 22.0) + (cyc - 6) * 2.0
        lines.append("%6d         welding_1  0.0000E+00 %3d %9.3E 0.000E+00 %3d %9.3E %8.2f     0     0 %9.2f %8d"
                     % (inc, cyc, rnd.uniform(0.01, 0.1), rnd.randint(3, 6), rnd.uniform(0.1, 1.0), prog, wall,
                        c["elements"]))
    lines += ["          job ends with exit number     %d" % c.get("exit", 3015), "          total wall time: %10.2f" % c["wall"],
              "          total cpu  time: %10.2f" % c["cpu"]]
    return HEADER_STS + "\n".join(lines) + "\n"


def out(c):
    rnd = random.Random(c["seed"] + 1)
    failed = c.get("time_step_error", True)
    head = ["# SYNTHETIC fixture written by make_synthetic.py -- not from any solver run. Same line formats as a",
            "# Simufact/MARC .out: error lines, one instance of each distinct warning text. Warning repeat counts:"]
    if failed:
        head += ["#     %d x      warning - Ignore recycling due to separation." % (c["warn_total"] - 6),
                 "#        3 x      warning - Unable to reduce time step below minimum of    1.00000E-02 specified in input.",
                 "#        3 x      warning - Search order is fixed due to glue control."]
    else:
        head += ["#     %d x      warning - Search order is fixed due to glue control." % (c["warn_total"] - 1),
                 "#        1 x      warning - Above post code is not supported in ARC."]
    head += ["#", ""]
    err = []
    for lo, hi in c["groups"]:
        for e in range(lo, hi + 1):
            err.append("             *** error - element inside out at element %9d integration point %5d"
                       % (e, rnd.randint(1, 8)))
    if c["repeat"]:
        e, n = c["repeat"]
        err += ["             *** error - element inside out at element %9d integration point %5d" % (e, 3)] * n
    if failed:
        err.append("             *** error - unable to reduce time step below minimum of    1.00000E-02")
        warn = ["*** warning - Ignore recycling due to separation.",
                "*** warning - Unable to reduce time step below minimum of    1.00000E-02 specified in input.",
                "*** warning - Search order is fixed due to glue control."]
    else:
        warn = ["*** warning - Search order is fixed due to glue control.",
                "*** warning - Above post code is not supported in ARC."]
    return "\n".join(head + err + [""] + warn + ["", "             Number of warning messages is %d" % c["warn_total"]]) + "\n"


def main():
    for name, c in CASES.items():
        base = os.path.join(HERE, FILES[name])
        io.open(base + ".sts", "w", encoding="latin-1", newline="\n").write(sts(c))
        io.open(base + ".out", "w", encoding="latin-1", newline="\n").write(out(c))
        print("wrote", base + ".sts/.out")


if __name__ == "__main__":
    main()
