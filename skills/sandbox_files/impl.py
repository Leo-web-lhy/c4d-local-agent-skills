"""Sandbox-confined file listing / reading. Refuses to escape the sandbox root."""
import os

MAX_READ = 4000
TEXT_EXT = {".txt", ".md", ".json", ".csv", ".py", ".log", ".yaml", ".yml"}


def _resolve(ctx, path):
    sandbox = os.path.realpath(ctx.get("sandbox") or os.path.join(os.getcwd(), "sandbox"))
    os.makedirs(sandbox, exist_ok=True)
    target = os.path.realpath(os.path.join(sandbox, path or "."))
    if target != sandbox and not target.startswith(sandbox + os.sep):
        return sandbox, None, "path escapes sandbox: %s" % target
    return sandbox, target, None


def run(args, ctx):
    sandbox, target, err = _resolve(ctx, str(args.get("path", "") or ""))
    if err:
        return {"ok": False, "error": err}
    mode = str(args.get("mode", "list") or "list").lower()
    if not os.path.exists(target):
        return {"ok": False, "error": "not found: %s" % os.path.relpath(target, sandbox)}

    if mode == "read":
        if os.path.isdir(target):
            return {"ok": False, "error": "read mode needs a file, got a directory"}
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(MAX_READ)
        return {"ok": True, "path": os.path.relpath(target, sandbox).replace("\\", "/"),
                "chars": len(text), "truncated": len(text) >= MAX_READ, "text": text}

    entries = []
    for root, _dirs, files in os.walk(target):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, sandbox).replace("\\", "/")
            item = {"path": rel, "bytes": os.path.getsize(full)}
            if os.path.splitext(fn)[1].lower() in TEXT_EXT:
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as f:
                        item["lines"] = sum(1 for _ in f)
                except OSError:
                    pass
            entries.append(item)
        if len(entries) > 200:
            break
    return {"ok": True, "root": os.path.relpath(target, sandbox).replace("\\", "/") or ".",
            "count": len(entries), "entries": entries[:200]}
