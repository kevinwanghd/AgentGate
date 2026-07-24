# MR 治理规范 v1 — Hermes Agent 指令

> 部署路径：仓库根 `.hermes.md`
> Hermes Agent v0.17.0 在会话启动时自动加载此文件（优先级高于 AGENTS.md / CLAUDE.md）。

---

## 任务开始前必读

在本仓库做任何代码修改前，先完成以下自检。

### 第零步：记录 AI 证据（每次编辑后）

本仓库的 AI 使用程度由机器自动统计，**不靠人或你主观判断**。你只需在每次成功修改一个源码文件后，向 `.governance/ai-evidence.jsonl` **追加一行** JSON：

```json
{"ts":"2026-06-26T10:00:00Z","tool":"hermes-agent","model":"hermes-agent@v0.17.0","file":"src/Foo.cs","added":80,"removed":3}
```

提交时 `collect_ai_usage.py` 会对照实际 diff 行数自动算出 `AI-Usage` 等级并写进 commit trailer。该文件已 gitignore，是会话产物。

### 第零步之二：跑测试留痕（改了生产代码必做）

改了生产代码就写/补单元测试，并用记录器跑（不要直接 `dotnet test`）：

```bash
python governance/scripts/record_test_run.py -- dotnet test X.Flow.sln --filter Category=Unit
```

它按真实退出码把结果记到 `.governance/test-evidence.jsonl`，提交时 hook 汇总成 `Tested:` trailer。本地自检：`python governance/scripts/check_tested.py --staged`。

放行任一即可：①有全绿记录且本次改了测试文件 ②加 `// risk:untested reason:"..." owner:@team reviewed:<今天>` 注解 ③命中 config 白名单。**带失败测试（`Tested: fail`）一律拒合。**

### 第一步：风险扫描

读取 `docs/governance/risk-types.md`，对照自己 diff 检查以下 8 类模式：

1. **auth-bypass** — 字面量 ID / 角色与认证字段比较
2. **magic-id** — 业务代码硬编码 ObjectId / UUID / 长数字串
3. **swallowed-exception** — catch 块既不 throw 也不 log
4. **suppressed-warning** — `#pragma warning disable` / `[SuppressMessage]` 等
5. **skipped-test** — `[Fact(Skip=...)]` / `[Ignore]` / `it.skip` 等新增或保留
6. **time-bypass** — DateTime 与字面量日期比较
7. **env-hardcode** — `if (env == "production")` 等
8. **todo-no-context** — TODO / FIXME 不含 `(owner, YYYY-MM-DD)` 元数据

### 第二步：命中则加注解

在命中行**上方**加一行（独立行，不是行内）：

```
// risk:<type> reason:"<理由，≥10字>" owner:@<团队> reviewed:<YYYY-MM-DD>
```

示例：

```csharp
// risk:auth-bypass reason:"机器人账号用于数据同步, 无人工登录路径" owner:@ad-platform reviewed:2026-06-25
if (adminUserId == "626786582b50ab8ec08b0fa0" || adminUserId == "64918ccaeb21944ec3ecf952")
```

**reason 禁止含**：临时 / 先这样 / 历史原因 / TODO / hack / wip / temp / for now

### 第三步：提交（trailer 自动生成）

仓库装了 `prepare-commit-msg` hook（`bash governance/scripts/install-hooks.sh`）后，commit 时会自动追加：

```
AI-Usage: heavy
AI-Tools: hermes-agent
AI-Models: hermes-agent@v0.17.0
AI-Lines: 92/127
Tested: pass (42/42)
```

你**不需要手填** AI-Usage / Tested。hook 缺失时手动补算：
`python governance/scripts/collect_ai_usage.py --staged --trailer-only`。

### 第四步：自动创建 MR（不要手填描述）

提交完后先自动生成 MR 描述，再用门禁校验通过后提交，你只需提供任务背景：

```bash
python governance/scripts/create_mr.py \
  --dry-run \
  --target-branch master \
  --why "<从用户原始需求提取的背景>" \
  > .governance/mr.md

sed '1,2d' .governance/mr.md \
  | python governance/scripts/validate_mr.py \
      --diff-base origin/master \
      --config governance.config.yml
```

脚本自动拼装：背景(--why)、变更内容(从 diff)、自测确认(从测试证据)、风险(自动评估)、AI 元数据(从 commit trailer，放折叠块)。加 `--dry-run` 可先预览。

> **MR 描述不靠人/AI 手填**。背景以外的段落全部从 git/trailer/测试证据自动推断。
> 原始 Markdown 必须保留 `## 背景`、`## 变更内容`、`## 自测确认`、`## 风险与回滚` 二级标题；普通文本标题不合规。

---

## 绝对禁止（CI 硬拒，不可绕过）

- 提交明文密钥 / token / 私钥
- 删除已有测试而不加 `risk:test-removal` 注解
- catch 静默吞异常而不加 `risk:swallowed-exception` 注解
- 修改以下路径须人工 approve，agent 不得自行合并：
  - `ci/`
  - `CODEOWNERS`
  - `.gitlab-ci.yml`
  - `governance.config.yml`
  - `charts*/`

---

## 参考文档

| 文件 | 用途 |
|---|---|
| `docs/governance/risk-types.md` | 8 类风险完整规则 + 注解示例 |
| `docs/governance/mr-spec.md` | MR 规范完整说明 |
| `.gitlab/merge_request_templates/default.md` | MR 描述模板（可直接复用） |
| `governance.config.yml` | enforcement 参数 |
| `governance/scripts/collect_ai_usage.py` | 汇总 AI 证据、自动算 AI-Usage trailer |
| `governance/scripts/install-hooks.sh` | 安装提交时自动写 trailer 的 git hook |
