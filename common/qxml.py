# -*- coding: utf-8 -*-
"""Qt qCompress blobs inside Simufact XML (robots_properties.xml <robot compressed="true">): decode / encode / patch.
Replaces the copies in patch_fullscript.py, build_fullscript_v2.py and check_short_v3.py. Pure Python 3.10+."""
import base64
import io
import re
import zlib

BLOB = re.compile(r'(<(robot)\b[^>]*\bcompressed="true"[^>]*>)([^<]+)(</\2>)')


def qdecode(b64):
    raw = base64.b64decode(b64.strip())
    data = zlib.decompress(raw[4:])
    if len(data) != int.from_bytes(raw[:4], "big"):
        raise ValueError("qCompress length header mismatch")
    return data.decode("utf-8")


def qencode(text):
    d = text.encode("utf-8")
    return base64.b64encode(len(d).to_bytes(4, "big") + zlib.compress(d, 9)).decode("ascii")


def read_text(path):
    return io.open(path, encoding="utf-8", newline="").read()


def robot_blobs(path):
    """Decoded XML text of every compressed <robot> element, in file order."""
    return [qdecode(m.group(3)) for m in BLOB.finditer(read_text(path))]


def replace_blobs(src, func):
    """Return (new_src, before_list, after_list); func(decoded_text, index) -> new decoded text."""
    before, after = [], []

    def rep(m):
        x = qdecode(m.group(3))
        y = func(x, len(before))
        before.append(x)
        after.append(y)
        return m.group(1) + qencode(y) + m.group(4)

    return BLOB.sub(rep, src), before, after


def strip_blobs(src):
    """Outer XML with blob payloads masked, for 'outer identical' self-checks."""
    return BLOB.sub(lambda m: m.group(1) + "#" + m.group(4), src)
