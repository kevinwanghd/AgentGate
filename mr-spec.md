# MR 治理规范 v1

适用范围：本仓库及全公司所有事业部代码仓库。
首版上线策略：**软启动**——除"风险注解"和"密钥扫描"外，其余字段缺失只警告不阻断；`soft_deadline` 到期自动转硬阻断。

---

## 1. 设计原则

1. **AI 写、AI 审、AI 合**——尽量不让人卡在流程上，规范由 CI 强制执行。
2. **基线轻量**——口头需求、无需求系统的事业部也能立刻执行。
3. **风险代码强制说明**——CI 扫到敏感模式必须有结构化注解，否则拒合。
4. **与 DeliverHQ 等上层流程共存**——通过 commit trailer + 文件契约联动，互不依赖。

---

## 2. 强制层（v1 起即硬阻断）

| 规则 | 触发条件 | CI 行为 |
|---|---|---|
| 风险注解契约 | `docs/governance/risk-types.md` 8 类模式命中 | 上方 5 行内缺结构化注解 / 字段不全 / 理由含黑名单词 / `reviewed:` 过 6 个月 → fail |
| 密钥扫描 | gitleaks 命中 | 直接 fail |
| 测试删除保护 | diff 删除 `[Fact]` / `[Test]` / `it(` / `test(` 等 | 缺 `risk:test-removal` 注解 → fail |

风险注解格式见 `docs/governance/risk-types.md`，是 AI agent 提交前的唯一参考文档。

---

## 3. 软提示层（v1 软，soft_deadline 到期转硬）

| 字段 | 要求 |
|---|---|
| M1 变更说明 | MR 描述含 `## 背景` 和 `## 变更内容` 两段，非空 |
| M2 AI 使用声明 | **自动采集**：`AI-Usage:` 由 git hook 在提交时写入 commit trailer，CI 从 commit 读取，人/AI 不手填 |
| M3 自测确认 | MR 描述含 `## 自测确认` 段，至少一行非空 |
| M4 大变更风险/回滚 | 满足"大变更"条件之一时，`## 风险与回滚` 段必填 |

**大变更判定**（任一即触发 M4）：
- 净增/改 ≥ 500 行（排除 `*.lock`、`*.Designer.cs`、`migrations/**`、自动生成文件）
- 触及高敏路径：`ci/`、`CODEOWNERS`、`charts*/`、`*secret*`、`.gitlab-ci.yml`
- 含 schema 变更：`*.sql`、`migrations/**`、`*.proto`

软模式期内，CI 仅以 warning 输出缺失项，不阻断合并。

---

## 4. AI 使用声明分档（机器自动判定，不靠人工）

AI 使用程度**不由任何人主观填写**。AI agent 开发时把每次编辑的客观证据（工具、模型、增删行数）追加到 `.governance/ai-evidence.jsonl`；提交时 `collect_ai_usage.py` 对照本次 commit 的真实 diff，按"AI 改动行 / 总改动行"占比自动算等级，写入 commit trailer。

| 档位 | 自动判定标准（占比） |
|---|---|
| `none` | 无 AI 证据，或本次无源码改动 |
| `light` | (0, 20%] |
| `medium` | (20%, 60%] |
| `heavy` | (60%, 100%] |
| `used` | 仅有补全类工具标记（Cursor Tab / Copilot 内联），无法精确测占比 |

**为什么不让人填**：手填会错档、会漏填、AI 事后也估不准。证据在开发当下采集、提交时按实际 diff 自动算，是唯一可信、可审计的来源。

**工具能力边界**：agentic 工具（Claude Code / Kiro / Hermes / Codex）逐次 Edit/Write，能精确自报行数；补全类工具（Cursor Tab、Copilot 内联）混在手敲里无法可靠区分，此时只标记"用了该工具"、等级记为 `used`，不伪造比例。

---

## 5. Commit Trailer 规范（机器可解析，自动生成）

跨事业部报表只 grep trailer，不解析自由文本描述。`AI-Usage` 等字段由 `prepare-commit-msg` hook 自动写入，形如：

```
AI-Usage: heavy
AI-Tools: claude-code
AI-Models: <模型名>
AI-Lines: 92/127
Requirement-ID: REQ-1234           (可选, 人工/DeliverHQ 填)
```

一次性安装 hook：`bash governance/scripts/install-hooks.sh`。
未装 hook 的仓库，CI 会退回读 MR 描述里的 `AI-Usage:`（兼容老 MR），并提示安装 hook。

DeliverHQ 用户：CR 编号写成 `Requirement-ID: CR-1234` 即可，套件自动按 `DeliverHQ/change-requests/CR-1234/` 解析。

---

## 6. 标题规范（建议，不强制阻断）

格式：`<type>: <简短描述>`

| type | 用途 |
|---|---|
| feat | 新功能 |
| fix | Bug 修复 |
| refactor | 重构，不改外部行为 |
| perf | 性能优化 |
| test | 测试相关 |
| docs | 文档 |
| chore | 工程化、依赖、配置 |
| ci | CI/CD |
| security | 安全修复 |

---

## 7. 软启动 → 硬阻断

`governance.config.yml` 中：

```yaml
metadata:
  enforcement: soft
  soft_deadline: 2026-09-30   # 距今最多 90 天
risk_annotations:
  enforcement: hard           # 第一天起就硬
  reviewed_max_age_days: 180  # 6 个月
```

约束：
- `soft_deadline` 距今 ≤ 90 天，install.sh 写超出范围会拒绝。
- 修改 `soft_deadline` 本身需走 MR；CI 校验通过 CODEOWNERS 锁治理负责人。

---

## 8. 与 DeliverHQ 共存

- install.sh 检测到仓库根有 `DeliverHQ/` 目录 → 自动开启 `deliverhq_integration: true`。
- 套件**只读** `DeliverHQ/evidence-summary.json`（如存在）作为 evidence 摘要；**不读** DeliverHQ 内部状态文件。
- DeliverHQ 写 commit trailer 用公开约定字段名（`Requirement-ID:` / `AI-Usage:`），不读套件 `governance.config.yml`。
- **MR 背景自动读取**：装了 DeliverHQ 时，`create_mr.py` 会按分支名里的 `CR-xxxx` 找到需求目录，从需求文档里提取"背景"段落作为 MR 的 `## 背景`——AI 连 `--why` 都不用传。读取路径、文件名、标题全部由 `governance.config.yml` 的 `requirement_doc_patterns` / `background_headings` 配置驱动，**不硬编码 DeliverHQ 内部格式**；读不到就回退要求 `--why`，绝不瞎编。
- 共享契约：commit trailer + `evidence-summary.json` + 配置化的需求文档读取（只读、可降级）。

---

## 9. CI 集成

`ci/governance.yml` 在 test 阶段之前运行，diff-only，秒级完成。包含两个 job：

- `governance:risk-scan` — 扫描风险模式 + 注解校验（硬）
- `governance:mr-validate` — 校验 MR 描述字段（v1 软）

CI 自检：核心规则文件带 checksum，被改弱则 governance job 失败。

---

## 10. 不包含什么（v1 边界）

为保证 v1 能广泛落地，以下**不**做：

- 强制 Requirement-ID
- 强制覆盖率阈值
- 强制双 agent 评审
- Roslyn analyzer（用 Python 通用扫描器先覆盖）
- 自动回滚策略

这些进 v2 讨论，按各事业部需求 opt-in。
