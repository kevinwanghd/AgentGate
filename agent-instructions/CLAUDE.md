# Agent 开发规范

本文件由 Claude Code / Kiro 在会话启动时自动加载。所有 AI agent 在本仓库开发时必须遵守以下规则。

---

## 提交代码前的自检清单（每次必做）

在创建 MR 或提交 commit 之前，按顺序完成：

1. **记录 AI 证据**：每次你用 Edit/Write 修改文件后，向 `.governance/ai-evidence.jsonl` 追加一行证据（见下方"AI 证据采集"）
2. **跑测试留痕**：改了生产代码就写/补单元测试，并用 `record_test_run.py` 跑（见下方"测试留痕"）
3. **风险扫描**：对照 `docs/governance/risk-types.md` 扫描自己的 diff
4. **补注解**：命中风险模式的代码，在上方加 `risk:*` 注解
5. **提交**：`AI-Usage` 和 `Tested` trailer 由 git hook 自动写入，**你不需要手填**
6. **创建 MR**：调 `create_mr.py --why "<任务背景>"` 自动生成并提交（见下方"自动创建 MR"），**不要手填 MR 描述**

---

## 自动创建 MR（核心：MR 描述是开发产物，不是事后填表）

你完成任务时已经知道"为什么改、改了什么、怎么测的"——这些信息开发时就有了，不该让你再对着空模板回忆手填。提交完 commit 后，用一条命令自动生成并提交 MR：

```bash
python governance/scripts/create_mr.py --why "<从用户原始需求提取的任务背景>"
```

脚本自动拼装 MR 描述：
- **## 背景** ← 你传的 `--why`（从用户需求提取，这是唯一需要你提供的）
- **## 变更内容** ← 从 git diff 自动生成文件清单 + 增删行数
- **## 自测确认** ← 从 `.governance/test-evidence.jsonl` 读测试结果
- **## 风险与回滚** ← 自动判断大变更/敏感路径/schema
- **治理元数据**（AI-Usage / Tested 等）← 从 commit trailer 读，放折叠块

提交前可加 `--dry-run` 预览描述。你**只需提供 `--why`**，其余全自动。

---

## 测试留痕（核心：没测过的生产代码不该提交）

改了生产代码（非测试、非 DTO/迁移/生成代码）就应该有单元测试。本仓库不靠"声称测过"，而靠**真实运行留痕**：用 `record_test_run.py` 包装测试命令，它执行测试、按退出码记录通过/失败到 `.governance/test-evidence.jsonl`，提交时 hook 把结果汇总成 `Tested:` trailer 写进 commit，CI 读 trailer 判断。

**你的职责**：写完/改完测试后，用记录器跑测试（不要直接 `dotnet test`）：

```bash
python governance/scripts/record_test_run.py -- dotnet test X.Flow.sln --filter Category=Unit
```

提交前可本地自检：

```bash
python governance/scripts/check_tested.py --staged
```

放行规则（满足其一）：
- 有一条全绿测试记录，**且**本次 MR 改动了测试文件（双重信号）
- 确实无法/不必单测的代码（DTO、启动引导、迁移），加注解豁免：
  `// risk:untested reason:"..." owner:@team reviewed:<今天>`（理由 ≥10 字，不含黑名单词，有效期 6 个月）
- 文件命中 `governance.config.yml` 的 `testing.exclude_paths` 白名单

**硬规则**：只要测试记录里有失败用例（`Tested: fail`），CI 无条件拒合——不许带红测试提交。

> 诚实边界：静态留痕证明"有没有测"，不证明"测得对不对"。它靠你如实运行 `record_test_run.py`。要真正的证明，未来会加 CI 差异覆盖率（见 `docs/governance/05-ci-reference.md`）。

---

## AI 证据采集（核心：AI 占比由机器自动算，不靠人或 AI 主观判断）

本仓库不要求任何人（包括你）主观判断"这次用了多少 AI"。你只需在开发当下如实记录每次编辑的客观证据，提交时由 `collect_ai_usage.py` 对照实际 diff 行数自动算出 `AI-Usage` 等级并写入 commit trailer。

**你的职责**：每次成功 Edit/Write 一个源码文件后，向 `.governance/ai-evidence.jsonl` **追加一行** JSON（不是覆盖）：

```json
{"ts":"2026-06-26T10:00:00Z","tool":"claude-code","model":"<你的模型名>","file":"src/Foo.cs","added":80,"removed":3}
```

字段说明：
- `ts` — ISO 时间戳
- `tool` — 固定填 `claude-code`（Kiro 填 `kiro`）
- `model` — 你的模型标识
- `file` — 被改文件的仓库相对路径
- `added` / `removed` — 本次该文件新增 / 删除的行数（你做这次编辑时清楚知道）

这个文件已被 `.gitignore`，是会话产物、不入库。等级映射（你无需自己算，脚本会做）：

| 占比 (AI改动行/总改动行) | 等级 |
|---|---|
| 0 | none |
| (0, 20%] | light |
| (20%, 60%] | medium |
| (60%, 100%] | heavy |

---

## 风险注解（硬规则，CI 会拒合）

命中 `docs/governance/risk-types.md` 里任意一类模式时，必须在该行**上方 5 行内**加注解。

**格式（单行）**：

```
// risk:<type> reason:"<理由>" owner:@<团队或个人> reviewed:<YYYY-MM-DD>
```

**示例**：

```csharp
// risk:auth-bypass reason:"机器人账号用于数据同步, 无人工登录路径" owner:@ad-platform reviewed:2026-06-25
if (adminUserId == "626786582b50ab8ec08b0fa0" || adminUserId == "64918ccaeb21944ec3ecf952")
```

**四个字段缺一不可**：
- `risk:<type>` — 必须是已注册类型（见 `docs/governance/risk-types.md`）
- `reason:"..."` — ≥ 10 字，**禁止**用：临时 / 先这样 / 历史原因 / TODO / hack / wip / temp / for now
- `owner:@xxx` — 该豁免的负责团队或个人
- `reviewed:YYYY-MM-DD` — 填今天日期，有效期 6 个月

**8 类必须注解的模式**（详细规则见 `docs/governance/risk-types.md`）：

| 类型 | 典型特征 |
|---|---|
| `auth-bypass` | 字面量 ID / 角色与认证字段比较 |
| `magic-id` | 业务代码硬编码 ObjectId / UUID / 长数字串 |
| `swallowed-exception` | catch 块既不 throw 也不 log |
| `suppressed-warning` | `#pragma warning disable` / `[SuppressMessage]` |
| `skipped-test` | `[Fact(Skip=...)]` / `[Ignore]` 等 |
| `time-bypass` | DateTime 与字面量日期比较 |
| `env-hardcode` | `if (env == "production")` 等 |
| `todo-no-context` | TODO/FIXME 不含 `(owner, YYYY-MM-DD)` |

删除已有测试时额外加：

```
// risk:test-removal reason:"用例已合并到 IntegrationTests.X" owner:@team reviewed:<今天>
```

---

## MR 描述结构（软规则，90 天后转硬）

MR 描述必须包含以下段落，缺少任一段落 CI 会警告：

```markdown
## 背景
（为什么要做这个变更）

## 变更内容
（改了什么，3-7 条要点）

## 自测确认
- [ ] 本地构建通过：`命令`
- [ ] 单元测试通过：`命令`
- [ ] 手动验证：步骤

## 风险与回滚
（大变更必填，小改动可写"低风险"）
```

> 注意：**MR 描述里不再写 AI 使用声明**。`AI-Usage` 由 commit trailer 自动携带，CI 直接从 commit 读取，人和 AI 都不手填。

---

## Commit Trailer（自动生成，勿手填）

`AI-Usage` 等 trailer 由 `prepare-commit-msg` git hook 在提交时自动写入，内容形如：

```
AI-Usage: heavy
AI-Tools: claude-code
AI-Models: <模型名>
AI-Lines: 92/127
```

你**不需要**手动加这些行。只要：
1. 仓库已运行 `bash governance/scripts/install-hooks.sh`（一次性安装 hook）
2. 你在开发时如实写了 `.governance/ai-evidence.jsonl`

hook 缺失时（如未安装），可手动补算：
```
python governance/scripts/collect_ai_usage.py --staged --trailer-only
```

需求关联仍可按需手填（可选）：

```
Requirement-ID: REQ-1234        (有需求时填，可选)
```

---

## 禁止事项（会被 CI 硬拒）

- 提交明文密钥、token、私钥（gitleaks 扫描）
- 带失败的测试记录提交（`Tested: fail` → CI 无条件拒合）
- 删除测试而不加 `risk:test-removal` 注解
- `#pragma warning disable` / `[SuppressMessage]` 而不加 `risk:suppressed-warning` 注解
- `catch` 块静默吞异常而不加 `risk:swallowed-exception` 注解
- 修改 `ci/`、`.gitlab-ci.yml`、`CODEOWNERS`、`charts*/`、`governance.config.yml` — 这些路径需要人工 approve（或治理负责人）

---

## 大变更判定（任一触发即需填"风险与回滚"）

- 净增/改 ≥ 500 行（排除 `*.lock`、`*.Designer.cs`、`migrations/**`）
- 触及高敏路径：`ci/`、`CODEOWNERS`、`charts*/`、`*secret*`、`.gitlab-ci.yml`
- 含 schema 变更：`*.sql`、`migrations/**`、`*.proto`

---

## 与 DeliverHQ 共存

如果本仓库有 `DeliverHQ/` 目录：
- `Requirement-ID:` trailer 填 `CR-xxxx`（而非 REQ-xxxx）
- 测试证据可以写 `Evidence: DeliverHQ/change-requests/CR-xxxx/verification-manifest.yml`
- CI 会自动读取 `DeliverHQ/evidence-summary.json`（如存在）

---

## 规范文件位置

| 文件 | 用途 |
|---|---|
| `docs/governance/risk-types.md` | 8 类风险模式详细规则 + 注解示例（提交前必读） |
| `docs/governance/mr-spec.md` | MR 规范完整说明 |
| `.gitlab/merge_request_templates/default.md` | MR 描述模板 |
| `governance.config.yml` | enforcement 参数（软/硬、阈值、路径） |
| `governance/scripts/collect_ai_usage.py` | 汇总 AI 证据、自动算 AI-Usage trailer |
| `governance/scripts/record_test_run.py` | 包装并记录测试运行，留痕到 test-evidence.jsonl |
| `governance/scripts/check_tested.py` | 检测改动的生产代码是否做过测试 |
| `governance/scripts/create_mr.py` | 自动生成并提交 MR（AI 只传 --why） |
| `governance/scripts/install-hooks.sh` | 安装提交时自动写 trailer 的 git hook |
| `.governance/ai-evidence.jsonl` | 你开发时追加证据的文件（已 gitignore） |
