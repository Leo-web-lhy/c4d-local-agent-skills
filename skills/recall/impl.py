"""Read back long-term memory: stored facts plus ranked past episodes."""

def run(args, ctx):
    mem = ctx.get("memory")
    if mem is None:
        return {"ok": False, "error": "memory store not available in ctx"}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "query is required"}
    facts = mem.facts_for(query, k=4)
    episodes = mem.search(query, k=4)
    return {
        "ok": True,
        "query": query,
        # facts_for() hands back raw rows (key/value/confidence), not the
        # episode shape, so the readable text has to be built here.
        "facts": [{"id": f.get("id"),
                   "key": f.get("key"), "value": f.get("value"),
                   "text": f.get("text") or ("%s: %s" % (f.get("key"), f.get("value"))),
                   "relevance": f.get("relevance") or f.get("confidence")} for f in facts],
        "episodes": [{"id": e.get("id"), "kind": e.get("kind"),
                      "text": e.get("text"), "relevance": e.get("relevance")}
                     for e in episodes],
        "counts": mem.stats(),
    }
