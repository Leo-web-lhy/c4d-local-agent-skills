# C4D AAR（事后复盘）

## 一句话结论

**做成了**：本机离线 `Qwen2.5-0.5B-Instruct` 驱动的 Agent，脚本可自选技能、跨会话记忆可用、沙箱读写有边界，三个演示会话全部 `stop=answered_intent` 且答案逐字正确。**但代价是**：让 0.5B 负责「综合答案」失败了 4 次，最后是我把答案组装权收回到代码里，只留下「选择调哪个技能」给模型。

## 一、目标与验收

| 项 | 目标 | 实测结果 | 判定 |
| --- | --- | --- | --- |
| 离线运行 | 不调云 API | 权重 988,097,824 B 落地本地，`generation` 全在进程内 | 达成 |
| 技能可用 | Agent 自主调用 ≥2 个技能 | 4 个技能全部加载，`load_errors=[]`；一次任务跑出 `['calc','remember','remember']` | 达成 |
| 跨会话记忆 | 会话 A 写、会话 B 读 | A: `name=Li Ang` / `language=Chinese`；B 召回两条 | 达成 |
| 沙箱安全 | 越界路径必须拒绝 | `../agent`、`C:/Windows` 均返回 `path escapes sandbox`，3/3 拦截 | 达成 |
| 无模型自检 | 不依赖权重也能验证技能层 | `[smoke] PASS skills=4 calc=3/3 guard=3/3 memory=True` | 达成 |
| 路由准确率 | 提示词改前/改后对照 | `old hit=2/6 rate=33.3%` → `final hit=6/6 rate=100.0%` | 达成 |

## 二、失败链（这是本次最有价值的部分）

五环，每一环都是真跑出来的，不是假设：

### 环 1：`FileNotFoundError: [Errno 2] No such file or directory`
模型把 trace 名（名字里含 `/`）当成文件路径传给了技能。技能层不做参数净化，错误直接穿透到 Agent。
**修**：技能注册表加参数校验与类型收敛，路径类参数统一走沙箱解析。

### 环 2：`KeyError: 'text'`
`memory.facts_for()` 返回的是 `key/value/confidence` 原始行，而 `recall` 的实现按 episode 的形状取 `f["text"]`。两处对同一个数据结构的假设不一致。
**修**：`recall/impl.py` 改为 `text = f.get("text") or "%s: %s" % (key, value)`，并把 `key`/`value` 一并透出。

### 环 3：`skills=[]` 空转
字段修好后，模型开始走另一个极端——直接给答案，一个技能都不调。
**修**：提示词写死语义边界（`Arithmetic, files and stored facts can ONLY be answered by a skill call.`）。A/B 从 2/6 提到 6/6。

### 环 4：`stop=step_budget` 同参死循环
准确率上来之后暴露出新问题：**技能调用没有终止条件**。

```
session-a stop=step_budget steps=3 skills=['calc','calc','calc']
session-b skills=['remember']
session-c skills=['sandbox_files','sandbox_files']
```

同一个调用、同样的参数，一直重复到预算耗尽。
**修**：`called` 集合按 `skill|json(args,sort_keys)` 去重 + 末步 `router.finalize()` 强制作答 + `_skill_hint()` 收窄提示。

### 环 5：模型综合答案错误 / 编造
死循环消失后，答案质量暴露出来：

- `answer: The sum of 847 and 39 is 33033.` —— 数字是 847×39，却说成「和」。
- `answer: There are 0 lines in the notes file.` —— 沙箱实有 2 个文件，凭空编造。
- `answer: The task requires fetching files from the sandbox, but there is no specific file to list.` —— 退化成空话。
- `answer: I am Li Ang, and you should answer in Chinese.` —— 用上下文注入的记忆冒充 `recall` 检索结果。

**修**：新增 `agent\intent.py`，高置信任务走确定性 `plan()` + `compose()`，命中即 `stop_reason="answered_intent"`；未命中才回退 LLM 循环。同时把「计划被执行上限切掉」的 bug 一并修掉（`plan[:max(1, max_steps-1)]` → 全量执行），否则会出现 3 个调用配 2 个结果、答案里冒出 `remember: no result`。

## 三、做对的四件事

1. **先建无模型自检**。`tests\smoke_no_model.py` 秒级跑完，把「技能本身有没有毛病」和「模型有没有毛病」彻底分开。没有它，环 2 那种字段契约错误会被误判成模型问题。
2. **A/B 而不是直觉**。提示词改动有 2/6 与 6/6 的实数对照，不是「感觉好多了」。
3. **给模型划权限边界，而不是换更大模型**。0.5B 在「选技能」上 6/6，在「组装答案」上 0/4。结论是分权，不是升级硬件——这样在 8GB 显存甚至无独显的机器上都能跑。
4. **越界即拒并留证**。`../agent` 和 `C:/Windows` 两个负例都写进 smoke 断言（`guard=3/3`），沙箱不是口头承诺。

## 四、做错的三件事

1. **一开始默认「路由对了就万事大吉」**。环 4、环 5 都是路由准确率提高之后才冒出来的，我把验证顺序搞反了——应该先测「终止条件」和「答案正确性」，再优化准确率。
2. **把 `max_steps` 调大当成解法**。4→6 只是把死循环从 2 圈变 4 圈，真正的解法是幂等去重 + 强制作答。调参数经常是在掩盖结构缺陷。
3. **intent 层第一版也被上限切了一刀**。3 个调用只跑了 2 个，答案里出现 `remember: no result`、`facts=1`。**这说明我改完代码后没有立刻核对「调用数 = 结果数」这个不变量**，只看了 `rc=0`。rc 为 0 完全不代表语义正确。

## 五、环境与工程噪声（真实记录，不粉饰）

- **本机 Python 控制台崩溃**：探针一次重跑出现 `rc_probe=-1073741819`（`0xC0000005`），不是断言失败，是本机写非 ASCII 到控制台的老毛病。该次结果**不采信**，以稳定跑出的 6/6 为准。规避法：脚本源码保持纯 ASCII、明细写 UTF-8 日志再读。
- **模型源只有一个能用**：HF 直连挂、hf-mirror 64KB/8.7s 不可接受、`?download=true` 42.9s 超时，最终只有 ModelScope repo 接口可用（206，`Content-Range: bytes 0-1048576/988097824`，0.5s）。这条如果一开始就按「国内镜像都差不多」去赌，会浪费大量时间。
- **GPU 白白闲置**：`RTX 4060 Laptop 8188 MiB` 在场但走的是 CPU（`cuda False`），加载 11.55s、常驻 2401.6 MB。这是有意选择（保证可移植），但确实丢了速度，应视为**已知取舍**而不是「优化空间」。

## 六、如果重来

1. **先写三条验收断言，再写 Agent**：调用数=结果数、答案不含 `None`/`no result`、跨会话召回必须命中。这三个比路由准确率重要得多。
2. **第一版就做 intent 层**。0.5B 的定位应该是「意图分类器 + 参数抽取」，不是「推理器」。我绕了四轮才接受这件事。
3. **把「技能执行」和「答案组装」放在两个可分别测试的类里**，从第一天起就能单独验证。

## 七、已知边界（不宣称）

- 只验证 `Qwen2.5-0.5B-Instruct` + CPU + Windows + Python 3.12 一条组合；GPU、量化、Linux/macOS 均未实跑。
- intent 层只覆盖高置信显式任务；开放式任务回退 LLM，质量受 0.5B 上限约束，不作承诺。
- 未接 function calling / MCP 协议，技能调度是自建注册表。
- 未做并发与长会话压力测试；记忆库当前 `episodes=12, facts=2`（演示规模），不是生产规模。

## 八、ΔR 归因

| 阶段 | 我原来的判断 | 实际 | 差距原因 |
| --- | --- | --- | --- |
| 起点 | 给 0.5B 好的提示词，它就能当 Agent | 能选技能，不能组装答案 | 低估了「综合」对模型规模的要求 |
| 中段 | 提高 max_steps 能解决重复调用 | 只是延长死循环 | 把结构缺陷当参数问题 |
| 后段 | rc=0 就说明跑通了 | `remember: no result` 藏在答案里 | 缺少语义不变量断言 |
| 终局 | — | 分权后 3/3 会话全对 | 收窄 AI 权限比升级硬件有效 |

**可迁移的一条**：小模型做 Agent 时，可靠性的关键不是「让模型做得更多」，而是**划清哪一步交给模型、哪一步交给代码**；并且每一步都要有一条不看模型脸色的断言。
