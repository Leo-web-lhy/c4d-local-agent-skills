"""Smoke test that needs no model: skill discovery, validation, sandbox guard, memory.

Console output is pure ASCII on purpose (the Windows console here crashes on
non-ASCII writes). Full detail goes to a UTF-8 report file.
"""
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.memory import Memory        # noqa: E402
from agent.skills import SkillRegistry  # noqa: E402

REPORT = os.path.join(os.path.dirname(ROOT), ".scratch", "smoke_report.txt")
BUF = io.StringIO()


def say(line=""):
    BUF.write(str(line) + "\n")


def main():
    say("=== registry ===")
    reg = SkillRegistry(os.path.join(ROOT, "skills"))
    say("names: %s" % reg.names())
    say("load_errors: %s" % reg.load_errors)
    say("schema: %s" % json.dumps(reg.schema(), ensure_ascii=False))
    say("validate calc({}) -> %s" % reg.validate("calc", {}))
    say("validate calc(ok) -> %s" % reg.validate("calc", {"expression": "1+1"}))
    say("validate unknown -> %s" % reg.validate("nope", {}))

    say("\n=== calc (AST whitelist) ===")
    calc_cases = ["847*39", "(12+30)/7", "2**10", "__import__('os')", "open('x')"]
    calc_ok = 0
    for expr in calc_cases:
        r = reg.call("calc", {"expression": expr})
        say("  %-20s -> %s" % (expr, json.dumps(r, ensure_ascii=False)))
        if expr in ("847*39", "(12+30)/7", "2**10") and r.get("ok"):
            calc_ok += 1
    say("  numeric pass: %d/3, unsafe rejected: %s" % (
        calc_ok,
        all(not reg.call("calc", {"expression": e}).get("ok")
            for e in ("__import__('os')", "open('x')"))))

    say("\n=== sandbox guard ===")
    guard_ok = 0
    for p in ["notes", "../agent", "C:/Windows"]:
        r = reg.call("sandbox_files", {"path": p})
        say("  %-12s -> ok=%s %s" % (p, r.get("ok"), r.get("error") or ("count=%s" % r.get("count"))))
        if p == "notes" and r.get("ok"):
            guard_ok += 1
        if p != "notes" and not r.get("ok"):
            guard_ok += 1
    say("  guard pass: %d/3" % guard_ok)

    say("\n=== sandbox read ===")
    say("  %s" % json.dumps(reg.call("sandbox_files", {"path": "notes/idea.txt", "mode": "read"}),
                            ensure_ascii=False))

    say("\n=== memory persistence (reopen same db) ===")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "mem.db")
    m1 = Memory(db)
    m1.add_fact("name", "Li Ang", source="user", confidence=0.95)
    m1.add_episode("t/a", "fact", "user name is Li Ang", tags="profile")
    m1.close()
    m2 = Memory(db)
    say("  facts_for: %s" % json.dumps(m2.facts_for("what is my name"), ensure_ascii=False))
    say("  search   : %s" % json.dumps([(h["kind"], h["text"]) for h in m2.search("name", k=3)],
                                       ensure_ascii=False))
    say("  stats    : %s" % json.dumps(m2.stats(), ensure_ascii=False))
    mem_ok = m2.stats()["episodes"] >= 1 and m2.stats()["facts"] >= 1
    m2.close()

    passed = (calc_ok == 3 and guard_ok == 3 and mem_ok
              and not reg.load_errors and len(reg.names()) == 4)
    say("\n[smoke] %s (skills=%d calc=%d/3 guard=%d/3 memory=%s)" % (
        "PASS" if passed else "FAIL", len(reg.names()), calc_ok, guard_ok, mem_ok))

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(BUF.getvalue())
    print("[smoke] %s skills=%d calc=%d/3 guard=%d/3 memory=%s report=%s" % (
        "PASS" if passed else "FAIL", len(reg.names()), calc_ok, guard_ok, mem_ok, REPORT))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
