"""Persist one (key, value) fact into the agent's SQLite memory."""

def run(args, ctx):
    mem = ctx.get("memory")
    if mem is None:
        return {"ok": False, "error": "memory store not available in ctx"}
    key = str(args.get("key", "")).strip().lower()
    value = str(args.get("value", "")).strip()
    if not key or not value:
        return {"ok": False, "error": "both key and value are required"}
    if len(key) > 64 or len(value) > 500:
        return {"ok": False, "error": "key<=64 and value<=500 chars"}
    mem.add_fact(key, value, source=ctx.get("session", "cli"), confidence=0.9)
    return {"ok": True, "stored": "%s = %s" % (key, value), "key": key}
