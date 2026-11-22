# C4D Agent 技能说明

本文件对应必交物里的「Agent 技能」：说明这台本地 Agent 到底具备哪些能力、能力边界在哪、
以及每个结论是被哪条命令证明的。所有引用的输出都来自本机实跑，不做修饰。

## 一、能力清单

| 技能 | 做什么 | 为什么不是让模型直接做 |
| --- | --- | --- |
| `calc` | 算术表达式求值，AST 白名单，禁用 `eval` | 0.5B 模型口算 `847*39` 会错；确定性计算交给 Python |
| `remember` | 把事实（key/value）与片段（episode）写入 SQLite | 上下文窗口装不下长期信息，写盘才能跨进程 |
| `recall` | 事实精确查 + 片段 BM25 检索（含时间衰减） | 让「我叫什么」这类问题不依赖模型记忆 |
| `sandbox_files` | 只读列出/读取沙箱内文件，`realpath` 拦截越界 | 让 Agent 能看文件但不越权，越界必须硬失败 |

四个技能均通过 `skills/<name>/SKILL.md` 声明、`impl.py` 实现，
由 `agent/skills.py::SkillRegistry` 自动发现，无需注册表硬编码。

## 二、参数契约（实跑输出）

```json
{"calc": {"expression": {"type": "string", "required": true}},
 "recall": {"query": {"type": "string", "required": true}},
 "remember": {"key": {"type": "string", "required": true},
              "value": {"type": "string", "required": true}},
 "sandbox_files": {"path": {"type": "string"}, "mode": {"type": "string"}}}
```

参数校验确实生效，不是摆设：

- `validate("calc", {})` → `["missing required arg 'expression'"]`
- `validate("calc", {"expression": "1+1"})` → `[]`
- `validate("nope", {})` → `["unknown skill 'nope'"]`

踩过的坑：`SKILL.md` front matter 的 `params` 必须写成**单行 JSON**，
写成嵌套 YAML 会被 `parse_front_matter` 读成 `{}`，参数校验静默失效——
这种「不报错但也不校验」的失效最难发现，所以上面三条断言被固定进冒烟测试。

## 三、安全边界（实跑输出）

算术沙箱：

| 输入 | 结果 |
| --- | --- |
| `847*39` | `ok=true`，`value=33033` |
| `(12+30)/7` | `ok=true`，`value=6` |
| `2**10` | `ok=true`，`value=1024` |
| `__import__('os')` | `ok=false`，`unsupported syntax: Call` |
| `open('x')` | `ok=false`，`unsupported syntax: Call` |

文件沙箱：

| 输入 | 结果 |
| --- | --- |
| `notes` | `ok=true`，2 个条目 |
| `../agent` | `ok=false`，`path escapes sandbox: …\C4D\agent` |
| `C:/Windows` | `ok=false`，`path escapes sandbox: C:\Windows` |

关键点：越界返回的是**解析后的绝对路径**，而不是一句模糊的「禁止访问」，
排查时能直接看到它想去的真实位置。拦截用 `realpath` 而非字符串前缀，所以
软链接和 `..` 都绕不过去。

## 四、记忆（跨进程验证）

冒烟测试写入后**关闭连接、重新打开同一个 db 文件**再读，证明数据在磁盘而不是进程内存：

```
facts_for("what is my name")  -> [{"key": "name", "value": "Li Ang", ...}]
search("name", k=3)           -> [("fact", "user name is Li Ang")]
stats()                       -> {"episodes": 1, "facts": 1, "sessions": 1, "fts": true, "size_kb": 40.0}
```

`fts=true` 表示 FTS5 可用、走 BM25 + 时间衰减；若某些环境 FTS5 缺失，
`memory.py` 自动回退 `LIKE`，功能不中断但排序质量下降（这一条在 AAR 里记为已知边界）。

## 五、全流程（模型参与的那部分）

技能本身不需要模型，真正体现「Agent」的是 `agent/loop.py` 的循环：
模型读任务 → 输出 `{skill, args}` 或 `{answer}` → 执行技能 → 把结果作为观察回填 →
再规划，直到给出答案或触到 `max_steps`。每一步连同模型原文、技能返回、耗时
写进 `demo/runs/run-*.jsonl`，可逐行复现当时的决策。

端到端的三段会话结果见 `demo/output/summary.json` 与 `AI日志.md`。
