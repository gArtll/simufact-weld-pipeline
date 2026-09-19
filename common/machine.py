# -*- coding: utf-8 -*-
"""The machine that will run the job: cores, memory, Simufact licence tokens, and a parallelism recommendation.

There is no repository default for solver domains and threads. What one machine ran well (a 16-thread request on a
20-core machine gave a CPU/wall ratio of 4.4 on one model) says nothing about the next one, and the licence may allow
fewer tokens than the cores suggest. The recommendation here is a starting point marked `machine_probe`; a short
smoke run turns it into `benchmark_measured`."""
import os
import re
import subprocess


def _run(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="ignore").stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def physical_cores():
    out = _run(["powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum"])
    m = re.search(r"\d+", out or "")
    if m:
        return int(m.group())
    out = _run(["lscpu"])                                   # non-Windows fallback
    c = re.search(r"Core\(s\) per socket:\s*(\d+)", out)
    s = re.search(r"Socket\(s\):\s*(\d+)", out)
    return int(c.group(1)) * int(s.group(1)) if c and s else None


def memory_gb():
    out = _run(["powershell", "-NoProfile", "-Command",
                "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)"])
    try:
        return float(out.strip().replace(",", "."))
    except ValueError:
        pass
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30, 1)
    except (AttributeError, ValueError, OSError):
        return None


def licence_tokens(lmutil=None, licence=None, feature="SF_WELDING_NODE"):
    """Tokens of `feature` the licence server issues, or None when it cannot be asked."""
    if not lmutil or not os.path.isfile(lmutil):
        return None
    cmd = [lmutil, "lmstat", "-a"] + (["-c", licence] if licence else [])
    out = _run(cmd, timeout=30)
    m = re.search(r"Users of %s:\s*\(Total of (\d+) licenses? issued" % re.escape(feature), out)
    return int(m.group(1)) if m else None


def probe(cfg=None):
    cfg = cfg or {}
    return {"logical_cores": os.cpu_count(), "physical_cores": physical_cores(), "memory_gb": memory_gb(),
            "licence_tokens": licence_tokens(cfg.get("lmutil"), cfg.get("licence_file"),
                                             cfg.get("licence_feature", "SF_WELDING_NODE")),
            "source": "machine_probe"}


def recommend(m, reserve_cores=2):
    """Domains x threads within physical cores (minus a reserve) and licence tokens.

    One domain up to 8 threads first: a direct sparse solver stops scaling around there, which is the one thing the
    measurement above does support. More domains only when there are cores left for them."""
    cores = (m.get("physical_cores") or m.get("logical_cores") or 1) - reserve_cores
    if m.get("licence_tokens"):
        cores = min(cores, int(m["licence_tokens"]))
    cores = max(1, cores)
    threads = min(8, cores)
    domains = max(1, min(4, cores // threads))
    return {"domains": domains, "threads_per_domain": threads, "source": "machine_probe",
            "basis": "physical cores %s - reserve %d, licence tokens %s; one domain saturates near 8 threads"
                     % (m.get("physical_cores"), reserve_cores, m.get("licence_tokens")),
            "confirm_with": "a smoke run of the case (benchmark_measured) before the long run"}
