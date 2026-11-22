"""Command line entry point for the local agent.

  python -m agent.cli ask "帮我算 847*39 并记住我叫李昂"
  python -m agent.cli ask "我叫什么" --session demo/session-b
  python -m agent.cli skills
  python -m agent.cli memory
"""
import argparse
import json
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from agent.local_llm import LocalLLM, load_config
    from agent.loop import Agent
    from agent import loop as loop_mod
else:
    from .local_llm import LocalLLM, load_config
    from .loop import Agent
    from . import loop as loop_mod

PROJECT_ROOT = loop_mod.PROJECT_ROOT
DEFAULT_CONFIG = os.path.join(PROJECT_ROOT, "config", "local_agent.json")


def build_agent(args):
    cfg = load_config(args.config or DEFAULT_CONFIG)
    model_dir = args.model_dir or cfg["model_dir"]
    if not os.path.isabs(model_dir):
        model_dir = os.path.join(PROJECT_ROOT, model_dir)
    llm = LocalLLM(model_dir, threads=args.threads or cfg.get("threads"),
                   quant=cfg.get("quant", "none"), verbose=args.verbose)
    return Agent(llm, max_steps=args.max_steps,
                 data_dir=os.path.join(PROJECT_ROOT, "data"),
                 trace_dir=os.path.join(PROJECT_ROOT, "demo", "runs"),
                 sandbox=os.path.join(PROJECT_ROOT, "sandbox"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="local-agent")
    p.add_argument("--config", default=None)
    p.add_argument("--model-dir", default=None)
    p.add_argument("--threads", type=int, default=None)
    p.add_argument("--max-steps", type=int, default=4)
    p.add_argument("--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="run one task through the agent loop")
    ask.add_argument("task")
    ask.add_argument("--session", default="cli")
    ask.add_argument("--json", action="store_true", help="print the full trace")

    sub.add_parser("skills", help="list loaded skills")
    sub.add_parser("memory", help="show memory row counts")

    args = p.parse_args(argv)
    agent = build_agent(args)

    if args.cmd == "skills":
        print(agent.registry.catalogue())
        for err in agent.registry.load_errors:
            print("[load-error]", err)
        return 0
    if args.cmd == "memory":
        print(json.dumps(agent.memory.stats(), ensure_ascii=False, indent=2))
        return 0

    trace = agent.run(args.task, session=args.session)
    if args.json:
        print(json.dumps(trace, ensure_ascii=False, indent=2))
    else:
        for step in trace["steps"]:
            if step.get("skill"):
                print("[step %d] %s(%s) -> ok=%s" % (
                    step["i"], step["skill"],
                    json.dumps(step["args"], ensure_ascii=False)[:120],
                    step["result"].get("ok")))
            elif step.get("invalid"):
                print("[step %d] invalid reply" % step["i"])
        print("-" * 60)
        print(trace["answer"])
        print("-" * 60)
        print("stop=%s steps=%d elapsed=%ss trace=%s" % (
            trace["stop_reason"], len(trace["steps"]), trace["elapsed_s"],
            os.path.basename(trace.get("trace_path", ""))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
