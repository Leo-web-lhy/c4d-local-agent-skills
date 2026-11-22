"""Agent loop: memory recall -> decide -> act -> observe -> answer.

Every step is appended to a JSONL trace so a reviewer can replay the run:
timings, token counts, the raw model reply and the exact tool observation.
"""
import json
import os
import time

from .memory import Memory
from .router import Router
from .skills import SkillRegistry

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Agent:
    def __init__(self, llm, skills_root=None, memory_path=None, data_dir=None,
                 max_steps=4, trace_dir=None, sandbox=None):
        self.llm = llm
        data_dir = data_dir or os.path.join(PROJECT_ROOT, "data")
        os.makedirs(data_dir, exist_ok=True)
        self.memory = Memory(memory_path or os.path.join(data_dir, "memory.sqlite3"))
        skills_root = skills_root or os.path.join(PROJECT_ROOT, "skills")
        self.registry = SkillRegistry(skills_root, ctx={
            "memory": self.memory,
            "sandbox": sandbox or os.path.join(PROJECT_ROOT, "sandbox"),
            "project_root": PROJECT_ROOT,
        })
        self.router = Router(llm, self.registry)
        self.max_steps = max_steps
        self.trace_dir = trace_dir or os.path.join(PROJECT_ROOT, "demo", "runs")
        os.makedirs(self.trace_dir, exist_ok=True)

    # ------------------------------------------------------------------ run
    def run(self, task, session="default", max_steps=None, memory_k=3, verbose=False):
        max_steps = max_steps or self.max_steps
        t_start = time.time()
        hits = self.memory.search(task, k=memory_k)
        facts = self.memory.facts_for(task, k=3)
        # episodes and facts come from different tables: episodes carry
        # (id, kind, text, relevance) while facts carry (key, value, confidence).
        # Normalise facts into the same context shape so the router prompt and
        # the trace share one field contract.
        ctx_items = list(hits) + [
            {"id": f["id"], "kind": "fact",
             "text": "%s: %s" % (f.get("key"), f.get("value")),
             "relevance": round(float(f.get("confidence") or 0.0), 4)}
            for f in facts
        ]
        self.memory.add_episode(session, "task", task, tags="task")

        observations = []
        steps = []
        nudges = 0
        answer = None
        stop_reason = "step_budget"
        called = set()
        from .intent import plan as intent_plan, compose as intent_compose
        plan = intent_plan(task)
        if plan:
            steps, observations = self._exec_plan(plan, session, max_steps)
            answer = intent_compose(task, plan, [s.get("result") for s in steps])
            stop_reason = "answered_intent"
        for i in range(0 if plan else max_steps):
            if i == max_steps - 1:
                # Step budget is spent: stop asking for skills and force a
                # grounded answer instead of ending on step_budget.
                decision = self.router.finalize(task, observations, ctx_items)
            else:
                decision = self.router.decide(task, observations, ctx_items)
            step = {
                "i": i + 1,
                "kind": decision["kind"],
                "metrics": decision["metrics"],
                "reply_rounds": len(decision["attempts"]),
            }
            if decision["kind"] == "answer":
                hint = self._skill_hint(task) if not observations else None
                if hint:
                    # A 0.5B router sometimes answers a data question straight
                    # from its weights. Once, per run, send it back for the
                    # skill instead of trusting the guess.
                    nudge = ("You answered without calling any skill, but this task "
                             "needs real data. Call a skill now: %s" % hint)
                    observations.append("Step %d: nudge -> %s" % (i + 1, nudge))
                    step["nudge"] = nudge
                    steps.append(step)
                    nudges += 1
                    continue
                answer = decision["payload"]["answer"]
                step["answer"] = answer
                steps.append(step)
                stop_reason = "answered"
                break
            if decision["kind"] == "skill":
                call = decision["payload"]
                sig = call["skill"] + "|" + json.dumps(call["args"], sort_keys=True, ensure_ascii=False)
                if sig in called:
                    step.update({"skill": call["skill"], "args": call["args"],
                                 "why": call.get("why", ""), "duplicate": True})
                    observations.append(
                        "Step %d: %s with identical arguments was already called; "
                        "reuse that observation instead of calling again."
                        % (i + 1, call["skill"]))
                    steps.append(step)
                    continue
                called.add(sig)
                result = self.registry.call(call["skill"], call["args"])
                step.update({"skill": call["skill"], "args": call["args"],
                             "why": call.get("why", ""), "result": result})
                obs = "Step %d: called %s with %s -> %s" % (
                    i + 1, call["skill"], json.dumps(call["args"], ensure_ascii=False)[:200],
                    json.dumps(result, ensure_ascii=False)[:400])
                observations.append(obs)
                self.memory.add_episode(
                    session, "tool", "%s %s => %s" % (call["skill"],
                                                      json.dumps(call["args"], ensure_ascii=False)[:160],
                                                      json.dumps(result, ensure_ascii=False)[:400]),
                    tags="tool " + call["skill"])
                steps.append(step)
                continue
            observations.append("Step %d: invalid reply rejected (%s)" % (i + 1, decision["payload"]))
            step["invalid"] = decision["payload"]
            steps.append(step)

        if answer is None:
            answer = ("Reached the %d-step budget without a final answer. Last observation: %s"
                      % (max_steps, observations[-1] if observations else "none"))
        self.memory.add_episode(session, "answer", answer, tags="answer")
        elapsed = round(time.time() - t_start, 2)
        trace = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session": session,
            "task": task,
            "stop_reason": stop_reason,
            "answer": answer,
            "steps": steps,
            "memory_hits": [{k: h[k] for k in ("id", "kind", "text", "relevance")} for h in ctx_items],
            "nudges": nudges,
            "elapsed_s": elapsed,
            "llm": self.llm.info(),
        }
        self._write_trace(trace)
        return trace

    def _exec_plan(self, plan, session, max_steps):
        """Run a planned skill sequence; the answer is rendered from these results."""
        steps, observations = [], []
        for i, call in enumerate(plan):
            result = self.registry.call(call["skill"], call["args"])
            steps.append({"i": i + 1, "kind": "skill", "skill": call["skill"],
                          "args": call["args"], "why": "deterministic intent plan",
                          "result": result, "metrics": {"source": "intent"},
                          "reply_rounds": 0})
            observations.append("Step %d: called %s with %s -> %s" % (
                i + 1, call["skill"], json.dumps(call["args"], ensure_ascii=False)[:200],
                json.dumps(result, ensure_ascii=False)[:400]))
            self.memory.add_episode(
                session, "tool", "%s %s => %s" % (
                    call["skill"], json.dumps(call["args"], ensure_ascii=False)[:160],
                    json.dumps(result, ensure_ascii=False)[:400]),
                tags="tool " + call["skill"])
        return steps, observations

    @staticmethod
    def _safe_name(session):
        """Session ids may look like "demo/session-a"; a slash would create a
        nested directory that does not exist, so flatten every separator."""
        out = str(session)
        for ch in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
            out = out.replace(ch, "-")
        return out.strip(". ") or "default"

    def _write_trace(self, trace):
        name = "run-%s-%s.jsonl" % (time.strftime("%Y%m%d-%H%M%S"), self._safe_name(trace["session"]))
        path = os.path.join(self.trace_dir, name)
        os.makedirs(self.trace_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(trace, ensure_ascii=False) + "\n")
        trace["trace_path"] = path
        return path

    @staticmethod
    def _skill_hint(task):
        """Return an actionable hint when a direct answer is suspicious.

        Cues are deliberately narrow so ordinary chat still answers directly.
        """
        t = (task or "").lower()
        has_digit = any(c.isdigit() for c in t)
        if has_digit and any(op in t for op in ("+", "-", "*", "/", "^", "%")):
            return "call calc with the expression from the task"
        for word in ("sandbox", "file", "files", "directory", "lines"):
            if word in t:
                return "call sandbox_files with path notes and mode list"
        if "remember that" in t or "name=" in t or "store" in t:
            return "call remember with the key and value"
        for word in ("my name", "my language", "what is my", "stored"):
            if word in t:
                return "call recall with the query"
        return None
