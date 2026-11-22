---
name: recall
description: Search long-term memory (past episodes and stored facts) for a query.
params: {"query": {"type": "string", "required": true}}
returns: {"ok": "boolean", "facts": "array", "episodes": "array"}
---
# recall

Reads back what `remember` and earlier turns wrote. Two sources are merged:

- `facts` — explicit `(key, value)` slots, matched by key and value text
- `episodes` — raw past turns, ranked by BM25 when the SQLite build ships FTS5,
  otherwise by a LIKE fallback plus a recency boost

Each hit carries an `id`, `kind`, `text` and a `relevance` score, so the caller
can cite where a memory came from instead of asserting it from nowhere. This is
what makes the second demo session able to answer questions about the first.
