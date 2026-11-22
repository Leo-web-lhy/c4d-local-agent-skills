---
name: calc
description: Evaluate one arithmetic expression offline and return the number.
params: {"expression": {"type": "string", "required": true}}
returns: {"ok": "boolean", "value": "number", "expression": "string"}
---
# calc

Evaluates a single arithmetic expression with Python's `ast` module instead of
`eval`, so only arithmetic nodes are permitted.

Allowed: `+ - * / // % **`, parentheses, unary minus, and the constants `pi`,
`e`. Anything else (names, calls, attribute access, comprehensions) is rejected
with `ok=false` and a reason naming the offending node type.

Used by the demo to show a real sandboxed capability the model cannot do
reliably on its own: a 0.5B model will happily claim `847 * 39` is `32013`.
