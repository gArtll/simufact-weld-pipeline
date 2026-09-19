# -*- coding: utf-8 -*-
"""ArcToolCmd result export (Format=4) and the reader for the CSV it writes.

CSV layout (2024.2):
    Connectivity (nb, type, nb of nodes);<elements>;<type>;<nodes per element>
    <element id>;<node id>;...                                (one line per element)
    Coordinates (nb);<nodes>
    <index>;<x>;<y>;<z>                                       (metres, one line per node)
    Post value (name, number);<NAME>;<nodes + 4>
    <value>                                                   (4 values first: two ranges, skipped)
    ...                                                       (then one value per node, node order)
A file can hold several "Post value" blocks; `Post=` selects them, and without it ArcToolCmd writes all of them.

POST_VALUES: the names of one Simufact Welding 2024.2 welding result file, listed by exporting without `Post=`
(docs/simufact-api-notes.md). Names not in this list are passed through: the list documents, it does not restrict."""
import os
import subprocess

import numpy as np

POST_VALUES = (
    "CONTACT", "NORSTS", "NORSTSX", "NORSTSY", "NORSTSZ", "TOTDISP", "EXTHEAT", "TEMPTURE", "XDIS", "YDIS", "ZDIS",
    "EFFPLS", "EFFSTS", "EFSTRT", "TXX", "TYY", "TZZ", "TXY", "TYZ", "TZX", "GLUE", "EQELS", "NDDM", "PKTEMP",
    "HFLUX-X", "HFLUX-Y", "HFLUX-Z", "ELERROR", "AREACHG", "ARCDISX", "ARCDISY", "ARCDISZ",
    "TEMPGRDX", "TEMPGRDY", "TEMPGRDZ", "EXTFILM", "HTC-CB", "FLWSTRES",
)
PREAMBLE = 4                       # values before the node values of a post value block (two ranges)


def export(arctool, arc, csv, posts=None):
    """ARC -> CSV. posts: sequence of post value names, None = all of them (much larger file)."""
    cmd = [arctool, "FileIn=" + arc, "FileOut=" + csv, "Format=4"]
    if posts:
        cmd.append("Post=" + ",".join(posts))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(csv):
        raise RuntimeError("ArcToolCmd failed on %s: %s %s" % (os.path.basename(arc), r.stdout[-300:], r.stderr[-300:]))
    return csv


def read(path, want=None):
    """Node coordinates in mm and the post value blocks. want: names to keep, None = all present."""
    lines = open(path, encoding="utf-8", errors="ignore").read().splitlines()
    ci = next(i for i, s in enumerate(lines) if s.startswith("Coordinates (nb)"))
    n = int(lines[ci].split(";")[1])
    xyz = np.array([[float(v) for v in lines[ci + 1 + k].split(";")[1:4]] for k in range(n)]) * 1000.0
    values = {}
    for i, s in enumerate(lines):
        if not s.startswith("Post value (name, number)"):
            continue
        name = s.split(";")[1]
        if want and name not in want:
            continue
        v = []
        for row in lines[i + 1:]:
            if not row.strip() or ";" in row:
                break
            v.append(float(row))
        if len(v) < PREAMBLE + n:
            raise ValueError("%s: post value %s has %d values, expected %d" % (path, name, len(v), PREAMBLE + n))
        values[name] = np.array(v[PREAMBLE:PREAMBLE + n])
    if want:
        missing = [k for k in want if k not in values]
        if missing:
            raise ValueError("%s: post values not in the file: %s" % (path, ", ".join(missing)))
    return xyz, values
