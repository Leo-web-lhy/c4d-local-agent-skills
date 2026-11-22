"""Decision layer: turn observations into either a skill call or a final answer.

Small local models are unreliable at strict JSON, so this module owns three
defences: a compact prompt, a brace scanner that survives prose around the
object, and a repair loop that feeds the parse error back to the model.
"""
import json
import re

SYSTEM_TMPL = """You are a local agent running fully offline on a laptop. You do not have internet access.
You can either call exactly one skill, or give the final answer.

Available skills:
{catalogue}

Reply with ONE JSON object and nothing else. Two legal shapes:
{{"action":"skill","skill":"<name>","args":{{...}},"why":"<short reason>"}}
{{"action":"answer","answer":"<final answer>"}}

Rules:
- Never invent a tool result; only state what an observation already showed.
- If a skill returned an error, either retry with fixed args or answer with what you know.
- Answer in the same language as the user's task.
- Do not nest JSON inside strings.
- Arithmetic, files and stored facts can ONLY be answered by a skill call.
- Common knowledge you already have needs no skill: answer it directly.
- remember stores a fact; recall fetches one. Storing uses remember, never recall.
- A task with several parts needs several skill calls, one per turn.
- remember is only for facts the user explicitly asks you to keep.

Worked examples (same shape, different task):
Task: Calculate 847*39
{{"action":"skill","skill":"calc","args":{{"expression":"847*39"}},"why":"arithmetic needs the calculator"}}

Task: Calculate 847*39, then remember that name=Li Ang and language=Chinese.
{{"action":"skill","skill":"calc","args":{{"expression":"847*39"}},"why":"do the arithmetic first, storing comes next"}}

Task: Remember that name=Li Ang
{{"action":"skill","skill":"remember","args":{{"key":"name","value":"Li Ang"}},"why":"user asked to store a fact"}}

Task: What is my name?
{{"action":"skill","skill":"recall","args":{{"query":"name"}},"why":"the value lives in memory, not in my weights"}}

Task: List the files in the sandbox
{{"action":"skill","skill":"sandbox_files","args":{{"path":"notes","mode":"list"}},"why":"file listing needs the sandbox skill"}}

Task: List the files in the sandbox and tell me how many lines each has.
{{"action":"skill","skill":"sandbox_files","args":{{"path":"notes","mode":"list"}},"why":"line counts live in the files, not in my weights"}}

Task: What is the capital of France?
{{"action":"answer","answer":"Paris"}}"""


def extract_json(text):
    """Return the first balanced JSON object found in text, or None."""
    if not text:
        return None
    start = None
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    chunk = text[start:i + 1]
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        start = None
                        continue
                    if isinstance(obj, dict):
                        return obj
    return None


def normalise(obj):
    """Map loose model output onto the two legal shapes; return (kind, payload)."""
    if not isinstance(obj, dict):
        return "invalid", "not a json object"
    action = str(obj.get("action") or obj.get("type") or "").strip().lower()
    skill = obj.get("skill") or obj.get("name") or obj.get("tool")
    if skill and action in ("skill", "tool", "call", "use", ""):
        args = obj.get("args") or obj.get("arguments") or obj.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return "invalid", "args is a string that is not json"
        if not isinstance(args, dict):
            return "invalid", "args must be an object"
        return "skill", {"skill": str(skill), "args": args,
                         "why": str(obj.get("why") or obj.get("reason") or "")[:120]}
    answer = obj.get("answer") or obj.get("final") or obj.get("response")
    if isinstance(answer, str) and answer.strip():
        return "answer", {"answer": answer.strip()}
    if action == "answer" and isinstance(obj.get("text"), str):
        return "answer", {"answer": obj["text"].strip()}
    return "invalid", "no 'skill' and no 'answer' key"


class Router:
    def __init__(self, llm, registry, max_new_tokens=192):
        self.llm = llm
        self.registry = registry
        self.max_new_tokens = max_new_tokens

    def finalize(self, task, observations, memory_hits=None):
        """Last step: force an answer grounded in what the skills already returned."""
        ctx = []
        if memory_hits:
            ctx.append("Relevant memories:\n" + "\n".join(
                "- (%s) %s" % (h.get("kind"), h["text"][:220]) for h in memory_hits))
        if observations:
            ctx.append("Steps so far:\n" + "\n".join(observations[-6:]))
        user = ("Task: %s\n\n%s\n\nThere is no step left. Reply with ONE JSON object: "
                '{"action":"answer","answer":"<short answer built only from these observations>"}'
                % (task, "\n\n".join(ctx) if ctx else "No steps yet."))
        messages = [{"role": "system", "content": self.system_prompt()},
                    {"role": "user", "content": user}]
        raw, metrics = self.llm.chat(messages, max_new_tokens=self.max_new_tokens)
        obj = extract_json(raw)
        kind, payload = normalise(obj) if obj is not None else ("invalid", "no json object found")
        attempts = [{"round": 1, "raw": raw[:600], "kind": kind, "metrics": metrics}]
        if kind == "answer":
            return {"kind": "answer", "payload": payload, "raw": raw,
                    "metrics": metrics, "attempts": attempts}
        text = (payload if kind == "invalid" else "") or ""
        if isinstance(obj, dict) and isinstance(obj.get("answer"), str):
            text = obj["answer"].strip()
        if not text:
            text = raw.strip()[:400] or "no answer produced"
        return {"kind": "answer", "payload": {"answer": "Grounded answer: " + text},
                "raw": raw, "metrics": metrics, "attempts": attempts}

    def system_prompt(self):
        return SYSTEM_TMPL.format(catalogue=self.registry.catalogue())

    def decide(self, task, observations, memory_hits=None):
        ctx = []
        if memory_hits:
            ctx.append("Relevant memories:\n" + "\n".join(
                "- (%s) %s" % (h.get("kind"), h["text"][:220]) for h in memory_hits))
        if observations:
            ctx.append("Steps so far:\n" + "\n".join(observations[-6:]))
        user = "Task: %s\n\n%s" % (task, "\n\n".join(ctx) if ctx else "No steps yet.")
        messages = [{"role": "system", "content": self.system_prompt()},
                    {"role": "user", "content": user}]
        attempts = []
        for round_no in range(3):
            raw, metrics = self.llm.chat(messages, max_new_tokens=self.max_new_tokens)
            obj = extract_json(raw)
            kind, payload = normalise(obj) if obj is not None else ("invalid", "no json object found")
            attempts.append({"round": round_no + 1, "raw": raw[:600], "kind": kind,
                             "metrics": metrics})
            if kind != "invalid":
                return {"kind": kind, "payload": payload, "raw": raw,
                        "metrics": metrics, "attempts": attempts}
            messages.append({"role": "assistant", "content": raw[:400]})
            messages.append({"role": "user", "content":
                             "That reply was rejected: %s. Reply with ONE JSON object only, "
                             "either {\"action\":\"skill\",...} or {\"action\":\"answer\",...}." % payload})
        return {"kind": "invalid", "payload": {"error": attempts[-1]["raw"][:200]},
                "raw": attempts[-1]["raw"], "metrics": attempts[-1]["metrics"],
                "attempts": attempts}


def repair_json_text(text):
    """Light cleanup for common model habits (trailing commas, quotes)."""
    text = re.sub(r",\s*([}\]])", r"\1", text or "")
    return text
