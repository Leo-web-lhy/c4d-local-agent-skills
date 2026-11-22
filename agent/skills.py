"""Skill registry: SKILL.md metadata + sandboxed python executors.

A skill is a directory under skills/ containing:
  SKILL.md   front matter with name / description / params (JSON) / returns
  impl.py    exposes run(args: dict, ctx: dict) -> dict

The registry is the only place that touches the filesystem on behalf of the
model; every executor receives a ctx with a sandbox root and the memory store.
"""
import importlib.util
import json
import os
import re
import time


def parse_front_matter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, text
    block, body = m.group(1), text[m.end():]
    meta, key = {}, None
    for line in block.splitlines():
        if not line.strip():
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*:\s*$", line):
            key = line.split(":")[0].strip()
            meta[key] = {}
        elif re.match(r"^\s+", line) and key:
            pass
        elif ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
            key = None
    raw = meta.get("params")
    if isinstance(raw, str):
        try:
            meta["params"] = json.loads(raw)
        except json.JSONDecodeError:
            meta["params"] = {}
    return meta, body


class SkillRegistry:
    def __init__(self, root, ctx=None):
        self.root = os.path.abspath(root)
        self.ctx = ctx or {}
        self.skills = {}
        self.load_errors = []
        self._load()

    def _load(self):
        if not os.path.isdir(self.root):
            return
        for name in sorted(os.listdir(self.root)):
            d = os.path.join(self.root, name)
            md = os.path.join(d, "SKILL.md")
            impl = os.path.join(d, "impl.py")
            if not (os.path.isdir(d) and os.path.exists(md) and os.path.exists(impl)):
                continue
            try:
                with open(md, "r", encoding="utf-8") as f:
                    meta, body = parse_front_matter(f.read())
                spec = importlib.util.spec_from_file_location("skill_" + name, impl)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if not hasattr(mod, "run"):
                    raise RuntimeError("impl.py has no run()")
                self.skills[meta.get("name", name)] = {
                    "meta": meta,
                    "body": body.strip(),
                    "module": mod,
                    "dir": d,
                }
            except Exception as exc:
                self.load_errors.append({"skill": name, "error": str(exc)[:200]})

    # -------------------------------------------------------------- prompts
    def catalogue(self):
        """Compact tool list injected into the decision prompt."""
        lines = []
        for name, s in self.skills.items():
            params = s["meta"].get("params") or {}
            pdesc = ", ".join(
                "%s(%s)" % (p, (spec or {}).get("type", "string") if isinstance(spec, dict) else "string")
                for p, spec in params.items()
            )
            lines.append("- %s: %s | args: %s" % (name, s["meta"].get("description", "").strip(), pdesc or "none"))
        return "\n".join(lines)

    def names(self):
        return sorted(self.skills)

    def schema(self):
        return {n: (self.skills[n]["meta"].get("params") or {}) for n in self.skills}

    # ------------------------------------------------------------- executor
    def validate(self, name, args):
        if name not in self.skills:
            return ["unknown skill '%s'" % name]
        params = self.skills[name]["meta"].get("params") or {}
        errs = []
        if not isinstance(args, dict):
            return ["args must be a JSON object"]
        for p, spec in params.items():
            spec = spec if isinstance(spec, dict) else {}
            if spec.get("required") and p not in args:
                errs.append("missing required arg '%s'" % p)
            if p in args:
                want = spec.get("type")
                val = args[p]
                ok = {"string": str, "number": (int, float), "boolean": bool,
                      "integer": int, "array": list, "object": dict}.get(want)
                if ok and not isinstance(val, ok):
                    errs.append("arg '%s' should be %s" % (p, want))
        return errs

    def call(self, name, args, timeout_s=None):
        t0 = time.time()
        if name not in self.skills:
            return {"ok": False, "error": "unknown skill '%s'" % name, "seconds": 0.0}
        errs = self.validate(name, args)
        if errs:
            return {"ok": False, "error": "; ".join(errs), "seconds": 0.0}
        ctx = dict(self.ctx)
        ctx.setdefault("sandbox", os.path.join(os.path.dirname(self.root), "sandbox"))
        try:
            result = self.skills[name]["module"].run(args if isinstance(args, dict) else {}, ctx)
            if not isinstance(result, dict):
                result = {"ok": True, "result": result}
            result.setdefault("ok", True)
        except Exception as exc:
            result = {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:240])}
        result["seconds"] = round(time.time() - t0, 3)
        result["skill"] = name
        return result
