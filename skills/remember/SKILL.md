---
name: remember
description: Store one durable fact about the user in long-term memory.
params: {"key": {"type": "string", "required": true}, "value": {"type": "string", "required": true}}
returns: {"ok": "boolean", "stored": "string"}
---
# remember

Writes a `(key, value)` pair into the agent's `facts` table, which survives
process restarts because it lives in a SQLite file on disk.

Use it when the user states something worth keeping across sessions: a name,
a preference, a constraint, a project code. `key` should be a short lowercase
snake_case slot name so a later session can look it up in a stable way.

The stored row also records `source` (the session id) and a confidence value,
so a future session can tell where the fact came from.
