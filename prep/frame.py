# -*- coding: utf-8 -*-
"""The local joint frame prep works in, instead of fixed global axes.

    normal  the standing part's thickness direction (the web normal); bead sides `neg` / `pos` lie along it and the
            web faces are the planes  normal . p = const
    along   the main direction of the weld; orders the root line and buckets the nodes for nearest-node searches

Both come from `inspect` (the joint frame of capability/classify.py) or from the case. Without either, prep uses the
legacy frame normal = +x, along = +z, which is what the regression cases were built in.

`coord(p, d)` is the component of p along d. For a coordinate axis it returns that coordinate itself, not a dot product
with zeros, so the legacy frame reproduces the regression outputs bit for bit."""
import math

LEGACY_NORMAL = (1.0, 0.0, 0.0)
LEGACY_ALONG = (0.0, 0.0, 1.0)


def parse_vec(text):
    v = tuple(float(x) for x in str(text).replace(";", ",").split(","))
    if len(v) != 3:
        raise ValueError("a direction needs 3 components: %r" % text)
    n = math.sqrt(sum(x * x for x in v))
    if n == 0.0:
        raise ValueError("zero direction: %r" % text)
    return tuple(x / n for x in v) if abs(n - 1.0) > 1e-12 else v


def axis_index(d):
    """0/1/2 when d is exactly +-x/+-y/+-z (a unit coordinate axis), else None."""
    for k in range(3):
        if abs(abs(d[k]) - 1.0) == 0.0 and all(d[i] == 0.0 for i in range(3) if i != k):
            return k
    return None


def coord(p, d):
    k = axis_index(d)
    if k is not None:
        return p[k] if d[k] > 0 else -p[k]
    return p[0] * d[0] + p[1] * d[1] + p[2] * d[2]


def check_frame(normal, along, tol=1e-6):
    """The two directions must be perpendicular: a weld running across the web thickness is not a fillet root."""
    dot = sum(a * b for a, b in zip(normal, along))
    if abs(dot) > tol:
        raise ValueError("frame normal %s and along %s are not perpendicular (dot %.3g)" % (normal, along, dot))


def is_legacy(normal, along):
    return tuple(normal) == LEGACY_NORMAL and tuple(along) == LEGACY_ALONG


def fmt(v):
    """A direction as a command-line value (no -0.0). Pass it as `--normal=<value>`: it may start with a minus."""
    return ",".join(repr(float(x) + 0.0) for x in v)
