"""A/B probe: does the router prompt make Qwen2.5-0.5B call skills?

variant "old"   = the first instruction-only prompt (kept here as a literal)
variant "final" = agent.router.SYSTEM_TMPL as shipped (rules + 5 worked examples)

The probe only measures the DECISION layer (which action the model picks); it
does not execute skills, so it is fast and the intent is unambiguous.

Console is ASCII only; full detail goes to ..\.scratch\probe_prompt.log (UTF-8).
"""
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.local_llm import LocalLLM, load_config      # noqa: E402
from agent.router import Router                       # noqa: E402
from agent.skills import SkillRegistry                # noqa: E402

LOG = os.path.join(ROOT, "..", ".scratch", "probe_prompt.log")
BUF = io.StringIO()


def log(line=""):
    BUF.write(str(line) + "\n")


OLD_TMPL = """You are a local agent running fully offline on a laptop. You do not have internet access.
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
- Do not nest JSON inside strings."""

TASKS = [
    ("Calculate 847*39", "calc"),
    ("Remember that name=Li Ang and language=Chinese", "remember"),
    ("What is my name?", "recall"),
    ("List the files in the sandbox and count the lines in each", "sandbox_files"),
    ("Compute (12+30)*7 with the calculator", "calc"),
    ("What is the capital of France?", "answer"),
]


class RouterOld(Router):
    def system_prompt(self):
        return OLD_TMPL.format(catalogue=self.registry.catalogue())


def run_variant(name, router):
    log("\n### variant=%s" % name)
    hits = 0
    rows = []
    for task, expect in TASKS:
        t0 = time.time()
        dec = router.decide(task, [], [])
        dt = time.time() - t0
        got = dec["payload"].get("skill") if dec["kind"] == "skill" else dec["kind"]
        ok = (expect == "answer" and dec["kind"] == "answer") or (got == expect)
        hits += 1 if ok else 0
        log("- task=%r expect=%s got=%s (%s) rounds=%d %.1fs"
            % (task, expect, got, "OK" if ok else "MISS", len(dec["attempts"]), dt))
        for a in dec["attempts"]:
            log("    r%d kind=%s raw=%r" % (a["round"], a["kind"], a["raw"][:260]))
        rows.append({"task": task, "expect": expect, "got": got, "ok": ok,
                     "rounds": len(dec["attempts"]), "seconds": round(dt, 2)})
    rate = 100.0 * hits / len(TASKS)
    log("### variant=%s hit=%d/%d rate=%.1f%%" % (name, hits, len(TASKS), rate))
    print("[probe] %s hit=%d/%d rate=%.1f%%" % (name, hits, len(TASKS), rate))
    return {"variant": name, "hit": hits, "total": len(TASKS), "rate": rate, "rows": rows}


def main():
    cfg = load_config(os.path.join(ROOT, "config", "local_agent.json"))
    model_dir = cfg["model_dir"]
    if not os.path.isabs(model_dir):
        model_dir = os.path.abspath(os.path.join(ROOT, model_dir))
    log("model_dir=%s" % model_dir)
    t0 = time.time()
    llm = LocalLLM(model_dir, threads=cfg.get("threads"), verbose=False)
    log("load_seconds=%.2f" % (time.time() - t0))
    registry = SkillRegistry(os.path.join(ROOT, "skills"), ctx={})
    log("skills=%s errors=%s" % (registry.names(), registry.load_errors))

    results = [run_variant("old", RouterOld(llm, registry)),
               run_variant("final", Router(llm, registry))]

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(BUF.getvalue())
    with open(LOG.replace(".log", ".json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("[probe] log=%s" % LOG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
