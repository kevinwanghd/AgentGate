# Agent 开发规范（GitHub thin 模式）

> 本段由 AgentGate 安装器按 `--platform github --mode thin` 生成，描述本仓库**真实**的
> GitHub CI 门禁。与 GitLab 模式不同：本仓库**没有** `governance/scripts/` 目录，
> 治理脚本只在 CI 运行时从中央 AgentGate 仓库 checkout 到 `.agentgate/`，不会复制到本仓库。
> 不要尝试在本地调用治理脚本或 GitLab MR 工具。

## 门禁入口

本仓库通过 `.github/workflows/agentgate.yml` 调用中央可复用工作流：

```
kevinwanghd/AgentGate/.github/workflows/agentgate.yml@github-stable
```

升级门禁只需更新中央 `github-stable`，本仓库无需复制任何脚本。

## 创建 PR（GitHub 流程）

用 `gh` 创建 PR，**不要**使用 GitLab `create_mr.py` 或 `glab`：

```bash
gh pr create --base <目标分支> --title "<type>: <中文描述>" --body-file <PR 描述文件>
```

PR 标题遵循 `<type>: <描述>`（feat/fix/refactor/perf/test/docs/chore/ci/security）。

## PR 描述（硬规则，mr-validate 校验）

**所有 PR 描述必须使用中文撰写**，且包含以下段落（保留 `##` 标题）：

```markdown
## 背景
（为什么做这个变更）

## 变更内容
（改了什么，3-7 条要点）

## 自测确认
- [ ] 已运行的命令/测试及结果
- [ ] 手动验证步骤

## 风险与回滚
（大变更必填；小改动写"低风险"）
```

- `## 背景`、`## 变更内容`、`## 自测确认`：每次必填。
- `## 风险与回滚`：**大变更**必填。大变更判定：净改动 ≥ 500 行、触及高敏路径
  （`ci/`、`.gitlab-ci.yml`、路径含 `secret` 等）或含 schema 变更
  （`*.sql`、`migrations/**`、`*.proto`）。
- `governance.config.yml` 的 `metadata.mandatory_fields` 含 `ai_usage`：`mr-validate`
  优先从本次 commit trailer 读取 `AI-Usage`，读不到时回退读 PR 描述。thin 模式**不安装
  git hook**，因此提交时需手动在 commit message 末尾写一行：

  ```
  AI-Usage: <none|light|medium|heavy>
  ```

  或在 PR 描述中写 `AI-Usage: <值>` 作为兜底。

## 门禁 job（由中央工作流执行）

| job | 检查内容 |
|---|---|
| `risk-scan` | 扫描本次 diff 的风险模式，命中的代码必须有 `risk:*` 注解 |
| `secret-scan` | 扫描本次改动引入的明文密钥 / token |
| `mr-validate` | 校验 PR 描述为中文且含必填段落；大变更校验风险回滚段 |
| `test-check` | **静态**检查测试证据，不运行测试；生产代码改动需合法 `risk:untested` 注解或命中 `testing.exclude_paths` |
| `go-test` | 仓库存在 `go.mod` 时跑受影响 Go 包测试；无 `go.mod` 自动跳过 |
| `gate-decision` | 汇总以上结果，决定自动合并 / 等待人工审批 / 阻断 |
| `auto-merge` | 仅当 `gate-decision` 返回 `AUTO_MERGE` 时开启 GitHub auto-merge |
| `pending-lessons-validate` | 校验 `.governance/pending-lessons/*.yml`；无文件时自动跳过 |

**测试由本仓库自己的 CI 负责**（例如 `.github/workflows/ci.yml` 里的 `make test` 或等价命令）。
`test-check` 是静态门禁，不会替你跑单元测试。GitHub thin 的 CI 里没有本地 test-evidence
（`testing.accept_tested_trailer: false` 时也不看 `Tested:` trailer），因此改动的生产代码
需要合法 `risk:untested` 注解，或让文件命中 `testing.exclude_paths`。真正跑测试的仍是
本仓库自己的 CI。

按风险等级要求的必过 job（`auto_merge.required_checks_by_risk`）：

- low：`risk-scan`、`secret-scan`、`mr-validate`
- medium / high / critical：额外要求 `test-check`

## 风险注解（硬规则，risk-scan 拒合）

命中风险模式时，必须在受影响代码**上方 5 行内**加注解。格式：

```
// risk:<type> reason:"<理由>" owner:@<团队或个人> reviewed:<YYYY-MM-DD>
```

（按语言使用 `//`、`#` 或 `<!-- -->` 注释前缀。）

字段要求：

- `risk:<type>` 必须是已注册类型。
- `reason:"..."` ≥ 10 个字符，禁用黑名单词（临时 / 先这样 / 历史原因 / TODO / 待确认 /
  quick fix / temp / wip / hack / for now）。
- `owner:@xxx` 为该豁免的负责人。
- `reviewed:YYYY-MM-DD` 为最近确认日期，距今 ≤ 90 天（过期后触碰该文件时需刷新）。

已注册类型：`auth-bypass`、`magic-id`、`swallowed-exception`、`suppressed-warning`、
`skipped-test`、`time-bypass`、`env-hardcode`、`todo-no-context`、`test-removal`、`untested`。

详细规则和示例见 `docs/governance/risk-types.md`。删除已有测试时用 `risk:test-removal`；
确无单测价值的生产代码用 `risk:untested`。thin 模式没有本地扫描器，`risk-scan` 在 CI 执行。

## 需要人工审批的路径（不要依赖自动合并）

`governance.config.yml` 的 `auto_merge.protected_paths` 命中的路径会被判定为 **critical**，
`gate-decision` 返回等待人工审批，不会自动合并。常见覆盖：

- `governance.config.yml`、`governance/**`
- `.github/workflows/**`、`.gitlab-ci.yml`、`ci/**`
- `CLAUDE.md`、`AGENTS.md`、`.hermes.md`、`.github/copilot-instructions.md`、`.cursor/rules/**`
- `CODEOWNERS`

以本仓库 `governance.config.yml` 的 `protected_paths` 为准。改到这些路径时，PR 需要人工
approve 并手动合并，不要反复重跑等待 auto-merge。

## 开工前必读 Lessons

开始任何修改前，读取 `governance/lessons/*.yml`（若存在）。thin 安装器只创建
`governance/lessons/repository.yml`（空 lessons 列表）：

- `enforcement: hard` 是不可违反约束。
- `enforcement: soft` 是仓库经验和操作提醒。

如果本次修复来自重复错误、门禁失败、线上事故或仓库约束误解，向
`governance/lessons/repository.yml` 追加经验。能用测试/扫描/门禁验证的写成 `hard` 并绑定
可执行检查；暂时只能提醒的写成 `soft`。

## 提交前自检

1. 读取 `governance/lessons/*.yml`（若存在）。
2. 改生产代码时同步写/补测试，并用本仓库自己的测试命令验证；确无单测价值的文件加合法
   `risk:untested` 注解（thin 的 `test-check` 以此放行）。
3. 对照 `docs/governance/risk-types.md` 扫描自己的 diff，命中模式补 `risk:*` 注解。
4. 提交：commit message 手动补 `AI-Usage: <none|light|medium|heavy>` trailer。
5. 用 `gh pr create` 创建 PR，描述用中文并包含必填段落。
6. 等门禁结果；命中 protected_paths 的变更请人工 approve 后手动合并。

## 规范文件位置

| 文件 | 用途 |
|---|---|
| `docs/governance/risk-types.md` | 风险模式详细规则 + 注解示例（提交前必读） |
| `docs/governance/mr-spec.md` | PR / MR 规范完整说明 |
| `governance.config.yml` | 门禁阈值、必填字段、保护路径、测试排除路径 |
| `governance/lessons/repository.yml` | 本仓库本地 lessons |
| `.github/workflows/agentgate.yml` | 指向中央 AgentGate 工作流的薄入口 |