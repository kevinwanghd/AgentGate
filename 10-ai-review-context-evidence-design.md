# 10 · AI 审查误报根治：上下文注入 + 事实预检 + 证据分级

> 状态：**待评审**（按 D21，确认后才拆卡开发）｜日期：2026-11-17
> 范围：`scripts/ai_review.py`（中央，protected）+ 消费仓 `ops/review-points.md` + 新增白名单。
> 触发：OpenMontage #72/#93/#103 反复被同类误报挡门，`--admin` 从例外变常态。

---

## 0. 根因（读代码确认，非猜测）

`ai_review.py:209` 喂给模型的只有两样：

```python
prompt = PROMPT.format(points=...) + f"\n\nchanges.txt:\n{diff}"
```

**只有 diff + 审查要点。** 于是三类误报是结构性必然：

| 误报 | 模型缺什么 | 为何必错 |
|---|---|---|
| "PyYAML 未声明" | 看不到 `requirements.txt`/`setup.py` | 只能猜 |
| "模块缺失" | 看不到目标分支文件树 | diff 里没有 = 以为不存在 |
| "新增编排违反架构" | 看不到 `STRATEGIES` 已有 20 条 | 只见新增行，判为"新决策"而非"扩展既有" |

放大器：
- `ai_review.py:250` `if findings: return fail` —— 任何 finding（含"可能 KeyError"式猜测）一票否决，无 severity。
- finding 只要 title+text 即成立，无证据要求；无白名单，同一误报每 PR 重来。

**本质**：把一堆**机器可判定的事实**丢给会幻觉的模型，还让它一票否决。违背"模型管判断、代码管确定性"的分工。

---

## 1. 设计原则

1. **事实归代码，判断归模型**：依赖是否声明、模块是否存在、模式是否已有——这些 `grep`/解析可定论的，绝不交给模型。
2. **模型必须看得见上下文**才能判断"新增 vs 扩展"。
3. **只有证据充分的 blocking 才挡门**；猜测降为 advisory（报告但放行）。
4. **误报要被记住**，不许复发。
5. **fail-closed 不放宽**：审查本身出错（鉴权/超时/截断/解析失败）仍判 fail；本设计只改"有 finding 时如何定性"，不改"审查没完成"的兜底。

---

## 2. 四层方案

### 层 1 · 确定性事实预检（纯代码，消灭大部分误报）

在调模型**前**，对 diff 做机器核查，产出"事实清单"注入 prompt，并对可证伪的指控直接拦截：

- **依赖事实**：解析目标分支 `requirements*.txt` / `setup.py` / `pyproject.toml` / `package.json`，得到已声明依赖集合。diff 新增 import 若已在集合内 → 记入事实"X 已声明于 file:line"。
- **模块存在事实**：对 diff 引用的本仓模块路径，`git cat-file -e <base>:<path>` 判存在 → 记入事实。
- **既有模式事实**：对被改符号（如某 dict/集合新增条目），统计目标分支中同结构已有条目数 → 记入事实"STRATEGIES 已有 N 条，本次为第 N+1 条"。

这些事实**注入 prompt**（让模型别再猜），同时作为**后置过滤器**：模型若仍报"未声明/缺失/新增模式"，用事实清单自动证伪丢弃。

> 实现为独立函数 `collect_repo_facts(base, files) -> dict`，可单测；不依赖模型。

### 层 2 · 上下文注入（让模型能判断）

prompt 除 diff 外追加：
- 每个被改文件的**完整内容**（超预算时取 enclosing 函数/类 + 前后 40 行）；
- 层 1 的事实清单；
- 依赖清单原文（当 import 变化时）。

预算控制：沿用 `--diff-cap`，新增 `--context-cap`；超限时按文件优先级裁剪并在 prompt 注明"上下文被截断"，让模型对缺失部分不下断言。

### 层 3 · 证据 + 分级契约（猜测不挡门）

改输出 schema：
```json
{"findings":[{
  "title":"文件:行号 问题",
  "severity":"blocking|advisory",
  "evidence":{"file":"...","line":N,"scenario":"输入X→错误输出Y"}
}]}
```
判定规则（代码侧，`classify_findings`）：
- `severity=blocking` **且** `evidence.scenario` 非空 **且** 未被层 1 事实证伪 **且** 未命中白名单 → 计入阻断。
- 其余（advisory / 无 scenario / 被证伪 / 白名单）→ 打印为 `::warning`，不阻断。
- prompt 明确要求：**给不出具体触发场景的，一律标 advisory**；"可能/或许/若未来"不得标 blocking。

> 兼容：模型若仍返回旧 schema（无 severity），按"无证据"处理 → advisory，不再一票否决。这本身就把猜测型误报降级。

### 层 4 · 白名单记忆（杜绝复发）

新增消费仓 `ops/review-allowlist.yml`：
```yaml
- rule: architecture-boundary
  file: "lib/review_strategies.py"
  fingerprint: "<sha8>"
  reason: "STRATEGIES 为既有数据驱动策略表，新增条目属扩展非新增编排"
  dismissed_by: "@kevinwanghd"
  date: "2026-11-17"
  pr: 103
```
- 审查前按 (rule, file, fingerprint) 过滤 findings。
- 提供 `scripts/dismiss_finding.py`：人工 `--admin` override 后落一条（含理由/人/日期/PR）。
- 白名单本身在 protected_paths，改它需人工审——防止"自己给自己豁免"。

---

## 3. 不变量（防自欺）

- 审查**没完成**（鉴权/网络/截断/JSON 解析失败）仍 `fail`，与本设计无关，不得放宽。
- 层 1 事实必须来自**目标分支**（`git show <base>:...`），不得用 PR 侧内容自证。
- 白名单只能由人工落，且文件受保护；模型/脚本不得自动写入。
- `blocking` 必须能被证据 scenario 复述；复核时若上下文不足以证实，降 advisory 而非硬挡。

---

## 4. 拆卡（确认后开发）

| 卡 | 内容 | 可机测验收 | 依赖 |
|---|---|---|---|
| **CARD-R1** | 层 1+2：`collect_repo_facts` + 上下文注入 + 事实后置过滤 | [机] 用真实误报 fixture：PyYAML 已声明→该 finding 被丢弃；模块存在→丢弃；STRATEGIES 扩展→降级。事实取自目标分支 | — |
| **CARD-R2** | 层 3+4：severity/证据契约 + `classify_findings` + 白名单 + `dismiss_finding.py` | [机] 无 scenario 的 blocking→advisory 不挡门；有 scenario 的真缺陷仍挡；白名单命中→丢弃；旧 schema 兼容降级；审查未完成仍 fail | CARD-R1 |

每卡用 **#72/#93/#103 的真实误报**当 fixture，先红后绿。

## 5. 风险与回滚

- 风险：上下文注入增大 token/时延 → `--context-cap` 限额 + 裁剪。
- 风险：severity 降级可能放过真缺陷 → 只有"给不出触发场景"才降；有场景的 blocking 不受影响；且层 1 只证伪**可机器判定**的指控，不碰逻辑/安全类判断。
- 回滚：`ai_review.py` 单文件 revert 即回到现状；白名单/事实清单为新增，删除无副作用。

## 6. 待确认

1. 四层方案与"事实归代码、判断归模型"的分工是否认可？
2. severity 降级规则（无具体触发场景→advisory）是否认可？这是"猜测不再挡门"的关键。
3. 白名单放消费仓 `ops/review-allowlist.yml` 且受保护，是否认可？
4. 拆 CARD-R1 / CARD-R2 两张卡，是否认可？