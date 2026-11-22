"""Two-session demo: does the agent remember across process boundaries?

Session A states facts and asks for arithmetic. Session B is a *separate*
session id that only has memory to go on, so correct recall proves the store
lives on disk rather than in the model context. Session C exercises the
sandbox-confined file skill.

Console output is ASCII-only on purpose: the Windows console in this
environment crashes on non-ASCII writes. Full detail goes to a UTF-8 log and
to demo/output/*.json.
"""
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.local_llm import LocalLLM, load_config      # noqa: E402
from agent.loop import Agent                          # noqa: E402

TASKS = [
    ("demo/session-a", "Calculate 847*39, then remember that name=Li Ang and language=Chinese."),
    ("demo/session-b", "What is my name, and which language should you answer in?"),
    ("demo/session-c", "List the files in the sandbox and tell me how many lines each has."),
]

BUF = io.StringIO()


def log(line=""):
    BUF.write(str(line) + "\n")


def _abs(rel):
    return rel if os.path.isabs(rel) else os.path.abspath(os.path.join(ROOT, rel))


def write_log(outdir):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "demo.log"), "w", encoding="utf-8") as f:
        f.write(BUF.getvalue())


def main():
    env_max_steps = int(os.environ.get("DEMO_MAX_STEPS", "3"))
    cfg = load_config(os.path.join(ROOT, "config", "local_agent.json"))
    model_dir = _abs(cfg["model_dir"])
    outdir = os.path.join(ROOT, "demo", "output")
    os.makedirs(outdir, exist_ok=True)      # json is written per session, before write_log()

    log("model_dir=%s" % model_dir)
    t0 = time.time()
    llm = LocalLLM(model_dir, threads=cfg.get("threads"), verbose=False)
    load_s = time.time() - t0
    log("load_seconds=%.2f" % load_s)
    try:
        info = llm.info()
    except Exception as exc:                                  # keep going, log why
        info = {"error": "%s: %s" % (type(exc).__name__, exc)}
    log("llm_info=%s" % json.dumps(info, ensure_ascii=False))

    agent = Agent(llm, max_steps=env_max_steps)
    names = agent.registry.names()
    log("skills=%s load_errors=%s" % (names, agent.registry.load_errors))

    summary = []
    for session, task in TASKS:
        log("\n=== %s ===" % session)
        log("task: %s" % task)
        try:
            trace = agent.run(task, session=session, max_steps=env_max_steps)
        except Exception as exc:
            log("RUN ERROR %s: %s" % (type(exc).__name__, exc))
            summary.append({"session": session, "task": task, "error": str(exc)})
            continue
        for step in trace["steps"]:
            if step.get("skill"):
                log("step %d: %s(%s) ok=%s" % (
                    step["i"], step["skill"],
                    json.dumps(step["args"], ensure_ascii=False)[:160],
                    (step.get("result") or {}).get("ok")))
            elif step.get("invalid"):
                log("step %d: invalid=%s" % (step["i"], step["invalid"]))
            elif step.get("answer"):
                log("step %d: answer_only" % step["i"])
        log("answer: %s" % trace["answer"])
        log("stop=%s elapsed_s=%.2f steps=%d trace=%s" % (
            trace["stop_reason"], trace["elapsed_s"], len(trace["steps"]),
            os.path.basename(trace.get("trace_path") or "")))
        name = session.replace("/", "-")
        with open(os.path.join(outdir, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=2)
        summary.append({"session": session, "task": task, "answer": trace["answer"],
                        "stop_reason": trace["stop_reason"], "steps": len(trace["steps"]),
                        "skills_used": [s.get("skill") for s in trace["steps"] if s.get("skill")],
                        "elapsed_s": trace["elapsed_s"]})

    stats = agent.memory.stats()
    log("\nmemory_stats=%s" % json.dumps(stats, ensure_ascii=False))
    with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"model": info, "load_seconds": load_s, "skills": names,
                   "runs": summary, "memory": stats}, f, ensure_ascii=False, indent=2)
    write_log(outdir)

    ok = sum(1 for r in summary if not r.get("error"))
    print("[demo] runs=%d/%d load=%.1fs memory=%s" % (
        ok, len(TASKS), load_s,
        json.dumps({k: stats.get(k) for k in ("episodes", "facts", "fts")}, ensure_ascii=False)))
    for r in summary:
        print("[demo] %s stop=%s steps=%s skills=%s" % (
            r["session"], r.get("stop_reason"), r.get("steps"), r.get("skills_used")))
    print("[demo] log=%s" % os.path.join(outdir, "demo.log"))
    return 0 if ok == len(TASKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
