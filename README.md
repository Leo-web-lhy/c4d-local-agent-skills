# C4D 本地大模型 Agent 技能

一个**完全离线**的本地 Agent：模型、规划、技能、记忆全部跑在本机，不调用任何云端 API，
运行时把 `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` 打开，加载权重用
`local_files_only=True`，断网也能回答。

- 底座模型：`Qwen/Qwen2.5-0.5B-Instruct`（988 MB safetensors，fp32，CPU 推理）
- 运行环境：Windows 11 / Python 3.12.13 / torch 2.15.0+cpu / transformers 5.24.0
- 硬件：i7-13700H（14C/20T），16.9 GB RAM，无 CUDA 可用（GPU 存在但本方案只用 CPU）

## 为什么值得看

0.5B 的小模型单独用是「能说人话但不保证算对」。这个项目的做法是：**让模型负责选技能，
让技能负责确定性**——算术走 AST 白名单求值而不是让模型口算，记忆走 SQLite 而不是压进上下文，
文件访问被锁在沙箱里。模型的弱被工程补上，而不是靠换大模型掩盖。

## 目录

```
agent/local_llm.py    加载权重、tokenize、生成；metrics 由 decode 步数实测
agent/router.py       把模型输出解析成 {skill, args} 或 {answer}
agent/skills.py       技能注册表：发现 skills/*/SKILL.md、校验参数、执行
agent/loop.py         ReAct 循环：规划 -> 执行技能 -> 观察 -> 再规划 -> 回答；落 trace
agent/memory.py       SQLite + FTS5(BM25 + 时间衰减)，失败自动回退 LIKE
agent/cli.py          ask / skills / memory 三个子命令
skills/calc           AST 白名单算术，禁用 eval
skills/remember       写入事实与片段
skills/recall         BM25 检索 + 事实精确查
skills/sandbox_files  只读沙箱内文件，realpath 拦截越界
demo/run_demo.py      三段会话：算术+记忆 -> 跨会话回忆 -> 沙箱清点
tests/smoke_no_model.py  不加载模型的冒烟测试（技能/沙箱/记忆落盘）
```

## 快速开始

```powershell
python -m agent.cli skills
python -m agent.cli ask "帮我算 847*39，并记住我叫李昂" --session demo/session-a
python -m agent.cli ask "我叫什么？" --session demo/session-b
python demo/run_demo.py
```

`ask` 会打印 `stop=<原因> steps=<步数> elapsed=<秒> trace=<文件名>`，
每次运行的完整轨迹落在 `demo/runs/run-*.jsonl`，可逐行回看模型原文与技能结果。

## 技能怎么写

`skills/<name>/SKILL.md` 的 front matter 里 `params` 必须是**单行 JSON**：

```
---
name: calc
description: Evaluate an arithmetic expression with a safe AST whitelist.
params: {"expression": {"type": "string", "required": true}}
---
```

`skills/<name>/impl.py` 暴露 `run(args: dict, ctx: dict) -> dict`，
`ctx` 里有 `memory` / `sandbox` / `project_root`。

## 已知限制（不粉饰）

- 0.5B 的指令遵循不稳，技能选择会偶发失手（见 `AAR.md` 的真实失败记录），
  失败时 Agent 会停在 `stop_reason=step_limit` 或回退成直接回答，不会假装成功。
- CPU 单机生成速度约几 token/s，所以 `max_new_tokens` 默认 192，长文场景不合适。
- 权重不入库（988 MB 超过仓库限制），首次使用需自行下载到 `config/local_agent.json` 的 `model_dir`。
