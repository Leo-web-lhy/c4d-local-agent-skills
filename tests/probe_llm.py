"""Step-by-step probe: find exactly where the model path dies.

Every line is flushed to a UTF-8 log immediately, so a hard process crash
still leaves evidence of the last completed step.
"""
import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG = os.path.join(os.path.dirname(ROOT), ".scratch", "probe_llm.log")
_fh = open(LOG, "w", encoding="utf-8", buffering=1)


def step(msg):
    _fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    _fh.flush()
    print("[probe] %s" % msg.replace("\n", " ")[:110].encode("ascii", "replace").decode("ascii"))


def main():
    step("01 start pid=%d" % os.getpid())
    import torch
    import transformers
    step("02 torch=%s cuda=%s transformers=%s" % (torch.__version__, torch.cuda.is_available(),
                                                  transformers.__version__))

    from agent.local_llm import LocalLLM, load_config
    step("03 imported local_llm")

    cfg = load_config(os.path.join(ROOT, "config", "local_agent.json"))
    step("04 config=%s" % json.dumps(cfg, ensure_ascii=False))
    model_dir = cfg["model_dir"]
    if not os.path.isabs(model_dir):
        model_dir = os.path.abspath(os.path.join(ROOT, model_dir))
    step("05 model_dir=%s exists=%s" % (model_dir, os.path.isdir(model_dir)))

    t0 = time.time()
    llm = LocalLLM(model_dir, threads=cfg.get("threads"), verbose=True)
    step("06 loaded in %.2fs" % (time.time() - t0))

    step("07 info=%s" % json.dumps(llm.info(), ensure_ascii=False))

    t1 = time.time()
    raw, metrics = llm.chat([{"role": "user", "content": "Reply with the single word: ready"}],
                            max_new_tokens=16)
    step("08 chat %.2fs raw=%r metrics=%s" % (time.time() - t1, raw[:120],
                                              json.dumps(metrics, ensure_ascii=False)))

    from agent.loop import Agent
    agent = Agent(llm, max_steps=2)
    step("09 agent skills=%s errors=%s" % (agent.registry.names(), agent.registry.load_errors))

    t2 = time.time()
    trace = agent.run("Calculate 12*12", session="probe/one", max_steps=2)
    step("10 run %.2fs stop=%s steps=%d" % (time.time() - t2, trace["stop_reason"],
                                            len(trace["steps"])))
    step("11 answer=%r" % trace["answer"][:200])
    step("12 trace=%s" % trace.get("trace_path"))
    step("DONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        _fh.write("CRASH\n" + traceback.format_exc())
        _fh.flush()
        raise
