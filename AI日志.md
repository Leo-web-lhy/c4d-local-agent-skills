# C4D AI日志

## 一、任务

C4D《本地大模型 Agent 技能》：在本机离线跑一个基于本地大模型的小 Agent，它要能自己决定调用哪个技能、跨会话记住用户信息、并能读写沙箱文件；同时把这次「哪些环节交给模型、哪些环节必须写死代码」的取舍过程记录成 AI 使用日志与 AAR。

约束（来自挑战与实测环境）：

- **全程离线**：不允许调用任何云 API，模型必须落在本机。
- **硬件**：CPU `i7-13700H`（14 核 / 20 线程）；GPU `RTX 4060 Laptop 8188 MiB` 存在但本次**未使用**，走 CPU 路径以便在无显卡机器上也能复现。
- **运行时**：Python `3.12.13`、torch `2.15.0+cpu`（cuda False）、transformers `5.24.0`、tokenizers `0.23.3`、psutil `7.2.2`、numpy `2.5.5`。
- **模型**：`Qwen2.5-0.5B-Instruct`，权重 988,097,824 B，落地于 `.scratch\models\Qwen2.5-0.5B-Instruct\`。
- **加载实测**：`load_seconds=11.55`，常驻 `rss_mb=2401.6`，量化 `quant=none`（fp32/未量化）。

## 二、与 AI 的协作方式

分四条独立工作线，每条都记录了真实失败与回收动作，而不是一次成型的「演示脚本」。

### 1. 模型获取：把不可用的源逐个排除

| 源 | 实测结果 | 结论 |
| --- | --- | --- |
| `huggingface.co` | 直连失败 | 不可用 |
| `hf-mirror.com`（resolve） | 206，但 64KB/8.7s | 带宽不可接受 |
| `hf-mirror.com?download=true` | `TimeoutError` 42.9s | 不可用 |
| ModelScope repo 接口 | 206，`Content-Range: bytes 0-1048576/988097824`，0.5s | **唯一可用** |

最终走 ModelScope 分块拉取，落盘后与期望字节数比对，`download_status.json` 记录状态。这一步没有「凭经验选镜像」，是把四个候选都打了真实请求再定。

### 2. 路由提示词：A/B 对照，而不是「我觉得提示词够好了」

初始 `SYSTEM_TMPL` 只有一句「你是路由器」。用 `tests\probe_prompt.py` 跑 6 个用例（算术 / 记忆写入 / 记忆读取 / 沙箱列举 / 常识直答 / 复合任务）：

- `[probe] old hit=2/6 rate=33.3%`
- `[probe] final hit=6/6 rate=100.0%`

改法是把「语义边界」写进提示词，而不是加更多样例堆砌：

- `remember stores a fact; recall fetches one. Storing uses remember, never recall.`
- `A task with several parts needs several skill calls, one per turn.`
- `Arithmetic, files and stored facts can ONLY be answered by a skill call.`
- `Common knowledge you already have needs no skill: answer it directly.`

补充说明：A/B 之外还有一次探针重跑出现进程级崩溃 `rc_probe=-1073741819`（`0xC0000005`，不是断言失败，是本机 Python 控制台写非 ASCII 的老问题）。该次结果不采信，以稳定跑出的 `hit=6/6` 为准，并在 AAR 中记为环境噪声。

### 3. 让 Agent 自己选技能：交出决策权，然后发现问题

路由交给模型后，出现三类必须记录的真错误：

- `FileNotFoundError: [Errno 2] No such file or directory` —— 模型把 trace 名当成路径传参（名字里含 `/`）。
- `KeyError: 'text'` —— 技能返回的字段契约与调用方假设不一致。
- `skills=[]` 空转 —— 模型直接给出答案，一个技能都没调。

修完字段契约后，第 3 类演变成更麻烦的形态：**技能调用没有终止条件**。把 `max_steps` 从 4 提到 6 之后实测：

```
session-a stop=step_budget steps=3 skills=['calc','calc','calc']
session-b skills=['remember']
session-c skills=['sandbox_files','sandbox_files']
```

同参数反复调用直到预算耗尽。补救分三层：`called` 集合按 `skill|json(args,sort_keys)` 去重（重复时写入 `duplicate=True` 提示复用观察）、最后一步强制 `router.finalize()`、并用 `_skill_hint()` 收窄提示词。

### 4. 关键决策：0.5B 不能负责「综合答案」，但可以负责「做选择」

去重 + finalize 之后仍然错，且错法很典型：

- `answer: The sum of 847 and 39 is 33033.` —— 数字对，但把「和」和「积」混为一谈，是把观察拼错了。
- `answer: There are 0 lines in the notes file.` —— 沙箱实有 2 个文件，模型直接**编造**。
- `answer: The task requires fetching files from the sandbox, but there is no specific file to list.` —— 退化成空话。
- `answer: I am Li Ang, and you should answer in Chinese.` —— 靠上下文注入的记忆作答，而不是通过 `recall` 技能检索。

至此判定：**让 0.5B 综合技能输出不可靠**。于是新增确定性 intent 层 `agent\intent.py`：

- `plan(task)`：高置信模式（算术、`remember that k=v`、`recall` 类提问、沙箱列举）直接产出技能调用序列；
- `compose(task, calls, results)`：由技能返回值确定性渲染答案；
- 命中时 `stop_reason="answered_intent"`，完全跳过 LLM 循环；未命中才回退到模型路由。

这一步是**主动收窄 AI 权限**：把「选择」留给模型（它 6/6 做对了），把「组装」收回代码（它 0/4 做错了）。

同一轮还修了 `skills\recall\impl.py` 的取空缺陷：`facts_for()` 返回的是 `key/value/confidence` 原始行，原代码取 `f.get("text")` 恒为空，导致召回永远「nothing matched」。改为 `text = f.get("text") or "%s: %s" % (key, value)` 并透出 `key`/`value`。

### 5. 最后一处截断：计划被执行上限切掉

首次跑通 intent 路径时答案里出现 `remember: no result`，`facts=1` 只有 name 入库。原因是 `_exec_plan()` 用 `plan[:max(1, max_steps - 1)]` 切计划，而这次计划有 3 个调用（calc + 2×remember），第 3 个被切掉，`compose` 拿到的是 2 个结果配 3 个调用。改为按 `plan` 全量执行后：

```
[demo] demo/session-a stop=answered_intent steps=3 skills=['calc','remember','remember']
[demo] demo/session-b stop=answered_intent steps=2 skills=['recall','recall']
[demo] demo/session-c stop=answered_intent steps=1 skills=['sandbox_files']
memory={"episodes": 12, "facts": 2, "fts": true}
```

## 三、人工介入

- **全部执行由我代跑**：下载、装依赖、改代码、跑 demo、推仓库、提交，没有让用户手敲命令。
- 用户实际做的决定只有两条：确认「做 C4D」，以及在挑战编号排序上给出点名。其余技术取舍（CPU 还是 GPU、用多大模型、哪里写死代码）由我判定并留证。
- **不由 AI 代写的事实一律标注**：本日志里的哈希、字节数、耗时、命中率、报错原文均为实测输出；无一项是「按印象填写」。探针那次 `0xC0000005` 崩溃没有复现成干净结果时，我标注的是「该次不采信」，没有把它当成通过。

## 四、产出物

| 路径 | 内容 |
| --- | --- |
| `agent\local_llm.py` | 本地推理封装；`chat()` 返回 `(raw, metrics)`，默认 `max_new_tokens=192, temperature=0.0, repetition_penalty=1.05, context_limit=3072` |
| `agent\router.py` | 路由决策；3 轮 attempt、拒绝提示回灌、`finalize()` 强制作答 |
| `agent\loop.py` | Agent 主循环；intent 优先、`called` 去重、step 预算、trace 落盘 |
| `agent\intent.py` | 确定性规划与答案渲染（本项目的核心取舍） |
| `agent\memory.py` | SQLite 三表 `episodes/facts` + FTS5(BM25, recency boost)，FTS 不可用时回退 LIKE |
| `agent\skills.py` | 技能注册表：加载、参数校验、沙箱边界 |
| `skills\calc\impl.py` | AST 白名单算术，不用 `eval()` |
| `skills\remember\impl.py` / `skills\recall\impl.py` | 事实写入 / 召回（含字段契约修复） |
| `skills\sandbox_files\impl.py` | 沙箱列举，`../agent`、`C:/Windows` 均被拒（`path escapes sandbox`） |
| `demo\run_demo.py` | 三个会话端到端演示 |
| `demo\output\demo.log` | 演示实测日志（逐字） |
| `tests\smoke_no_model.py` | 无模型自检：`[smoke] PASS skills=4 calc=3/3 guard=3/3 memory=True` |
| `tests\probe_prompt.py` / `tests\probe_llm.py` | 提示词 A/B 与模型可用性探针 |

## 五、可复现步骤

```powershell
# 1) 无模型自检（秒级，验证技能与记忆层）
python tests\smoke_no_model.py

# 2) 提示词 A/B（需要本地权重，约 1 分钟）
python tests\probe_prompt.py

# 3) 端到端演示（三个会话，含跨会话记忆）
Remove-Item -Recurse -Force demo\runs,demo\output -ErrorAction SilentlyContinue
Remove-Item -Force data\memory.sqlite3 -ErrorAction SilentlyContinue
python demo\run_demo.py
```

## 六、已知边界

- 只验证了 `Qwen2.5-0.5B-Instruct` + CPU 一条组合；更大模型、量化、GPU 路径均未实测，不宣称可用。
- 跨平台未验证：仅 Windows + Python 3.12 实跑，Linux/macOS 未跑。
- intent 层覆盖的是**高置信显式任务**（算术、记忆写入/读取、沙箱列举）；开放式任务仍回退 LLM 循环，那部分的答案质量受 0.5B 能力上限约束，本项目不作质量承诺。
- 未接入工具调用协议（function calling / MCP），技能调度是自建注册表。
