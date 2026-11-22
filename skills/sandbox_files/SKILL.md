---
name: sandbox_files
description: List or stat files inside the agent sandbox directory only.
params: {"path": {"type": "string"}, "mode": {"type": "string"}}
returns: {"ok": "boolean", "entries": "array"}
---
# sandbox_files

The only skill that touches the filesystem, and it refuses to leave the
sandbox root. `path` is resolved against the sandbox and then checked with
`realpath`, so `..` traversal and absolute paths outside the root are rejected
with `ok=false` and the offending resolved path in the error.

`mode` is `list` (default) or `read`. In `list` mode it returns each entry's
relative path, size and line count for text files. In `read` mode it returns
the first 4000 characters of one file, so a reviewer can see the agent only
ever sees what the sandbox exposes.
