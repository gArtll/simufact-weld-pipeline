# -*- coding: utf-8 -*-
"""Step manifests (inputs with sha256, outputs, duration, exit code) and small shared helpers."""
import hashlib
import io
import json
import os
import time


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_inputs(paths):
    out = {}
    for p in paths:
        if p and os.path.isfile(p):
            out[p] = sha256(p)
        elif p and os.path.isdir(p):
            for root, _, files in os.walk(p):
                for fn in files:
                    fp = os.path.join(root, fn)
                    out[fp] = sha256(fp)
        elif p:
            out[p] = None
    return out


class Manifest:
    def __init__(self, step_dir, step):
        self.path = os.path.join(step_dir, "manifest.json")
        self.data = {"step": step, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "inputs": {}, "outputs": [],
                     "checks": {}, "commands": [], "exit_code": None, "duration_s": None}
        self.t0 = time.time()
        os.makedirs(step_dir, exist_ok=True)
        self.save()

    def inputs(self, paths):
        self.data["inputs"].update(hash_inputs(paths))
        self.save()

    def check(self, name, ok, detail=None):
        self.data["checks"][name] = {"ok": bool(ok), "detail": detail}
        self.save()
        return ok

    def command(self, cmd, exit_code, log):
        self.data["commands"].append({"cmd": cmd, "exit_code": exit_code, "log": log})
        self.save()

    def finish(self, exit_code, outputs):
        self.data["exit_code"] = exit_code
        self.data["duration_s"] = round(time.time() - self.t0, 2)
        self.data["outputs"] = sorted(set(self.data["outputs"]) | set(outputs))
        self.data["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    def save(self):
        io.open(self.path, "w", encoding="utf-8").write(json.dumps(self.data, ensure_ascii=False, indent=1) + "\n")


def tail(path, n=20):
    if not path or not os.path.isfile(path):
        return "(no log %s)" % path
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return "\n".join(io.open(path, encoding=enc).read().splitlines()[-n:])
        except UnicodeDecodeError:
            continue
    return ""
