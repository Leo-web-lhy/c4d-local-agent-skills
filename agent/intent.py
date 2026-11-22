"""Deterministic intent planning and answer rendering for explicit tasks.

Measured on this laptop: a 0.5B router picks the right skill most of the time
once examples are added, but it cannot be trusted to assemble the final answer
from raw skill output. Two observed failures: it reported 847*39 as "the sum
of 847 and 39", and it claimed the sandbox was empty while the listing had
just returned two files. So the high-confidence patterns are planned and
rendered here, by code; anything else falls back to the language model loop.
"""
import re

_CALC_RE = re.compile(r"\d+(?:\.\d+)?(?:\s*[-+*/^%]\s*\d+(?:\.\d+)?)+")
_SPLIT_RE = re.compile(r"[;,]|\s+and\s+")


def _arith(task):
    m = _CALC_RE.search(task)
    if not m:
        return None
    expr = m.group(0).replace(" ", "")
    if not any(op in expr for op in "+-*/^%"):
        return None
    return {"skill": "calc", "args": {"expression": expr}}


def _remember(task, low):
    """Extract key=value pairs from a 'remember that ...' clause."""
    tail = task[low.find("remember"):]
    tlow = tail.lower()
    if "that" in tlow:
        tail = tail[tlow.find("that") + 4:]
    calls = []
    for chunk in _SPLIT_RE.split(tail):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip().strip(".").lower()
        value = value.strip().strip(".").strip()
        if key and value:
            calls.append({"skill": "remember", "args": {"key": key, "value": value}})
    return calls[:3]


def plan(task):
    """Return an ordered list of skill calls, or [] when the task is open-ended."""
    task = task or ""
    low = task.lower()
    calls = []
    hit = _arith(task)
    if hit:
        calls.append(hit)
    if "remember" in low:
        calls.extend(_remember(task, low))
        return calls
    memory_question = any(w in low for w in
                          ("my ", "what is", "which", "stored", "do you know"))
    if memory_question:
        for kw in ("name", "language"):
            if kw in low:
                calls.append({"skill": "recall", "args": {"query": kw}})
        if calls:
            return calls
    if "sandbox" in low and ("file" in low or "list" in low):
        calls.append({"skill": "sandbox_files", "args": {"path": "", "mode": "list"}})
    return calls


def _render(skill, res):
    if not isinstance(res, dict):
        return "%s: no result" % skill
    if skill == "calc":
        if res.get("ok"):
            return "%s = %s" % (res.get("expression"), res.get("value"))
        return "calc failed: %s" % res.get("error")
    if skill == "remember":
        if res.get("ok"):
            return "remembered %s" % res.get("stored")
        return "remember failed: %s" % res.get("error")
    if skill == "recall":
        facts = res.get("facts") or []
        texts = [f.get("text") for f in facts if f.get("text")]
        if texts:
            return "stored [%s] %s" % (res.get("query"), "; ".join(texts))
        return "stored [%s] nothing matched" % res.get("query")
    if skill == "sandbox_files":
        if not res.get("ok"):
            return "sandbox_files failed: %s" % res.get("error")
        entries = res.get("entries") or []
        if not entries:
            return "sandbox root: 0 files"
        rows = []
        for e in entries:
            if "lines" in e:
                rows.append("%s - %s lines" % (e.get("path"), e.get("lines")))
            else:
                rows.append("%s - %s bytes" % (e.get("path"), e.get("bytes")))
        return "sandbox root: %s file(s); %s" % (len(entries), "; ".join(rows))
    return "%s -> %s" % (skill, res)


def compose(task, calls, results):
    """Render the final answer from real skill results, without asking the model."""
    parts = []
    for i, call in enumerate(calls):
        res = results[i] if i < len(results) else None
        parts.append(_render(call["skill"], res))
    return "\n".join(parts) if parts else "no skill result to report"
