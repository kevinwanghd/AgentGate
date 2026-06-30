# AgentGate · MR 治理规范 v1

> 让 AI agent 自主写代码、自主提交、自主合并，**靠 CI 而非人工**把控质量；全程自动留痕（AI 用量 / 测试 / 风险注解），第一版不对现有开发流程造成强制阻碍。

全公司各事业部统一使用，一键安装，5 分钟接入。

---

## 完整操作手册（按角色阅读）

详细的安装与使用文档在 `docs/` 目录，按角色分册：

| 文档 | 读者 | 内容 |
|---|---|---|
| [docs/00-overview.md](docs/00-overview.md) | 所有人 | 总览、两层 enforcement 模型、文件地图 |
| [docs/01-gitlab-admin-setup.md](docs/01-gitlab-admin-setup.md) | GitLab 管理员 | 规范仓库、分支保护、MR 审批、CODEOWNERS、CI 模板 |
| [docs/02-repo-onboarding.md](docs/02-repo-onboarding.md) | 仓库负责人 | 一键安装到业务仓库、接入 CI、验证 |
| [docs/03-developer-terminal-setup.md](docs/03-developer-terminal-setup.md) | 开发者 | AI 工具安装、commit trailer 模板、本地预检 |
| [docs/04-ai-agent-workflow.md](docs/04-ai-agent-workflow.md) | AI agent / 开发者 | 每次提交的标准流程、注解写法、排错 |
| [docs/05-ci-reference.md](docs/05-ci-reference.md) | 所有人 | CI 门禁行为、读懂报错、申请豁免 |

下面是 5 分钟快速上手版。

---

## 目录结构

```
governance/
├── install.sh                    # 一键安装脚本
├── README.md                     # 本文件（快速上手）
├── mr-spec.md                    # MR 规范说明（人读）
├── risk-types.md                 # 风险注解目录（AI agent 提交前参考）
├── templates/
│   └── merge_request_default.md  # MR 模板源文件
├── agent-instructions/           # 各 AI 工具指令文件源
│   ├── CLAUDE.md
│   ├── hermes-instructions.md
│   ├── copilot-instructions.md
│   └── cursor-rules.mdc
└── docs/                         # 完整操作手册（见上表）
    ├── 00-overview.md
    ├── 01-gitlab-admin-setup.md
    ├── 02-repo-onboarding.md
    ├── 03-developer-terminal-setup.md
    ├── 04-ai-agent-workflow.md
    └── 05-ci-reference.md
```

安装后写入目标仓库的位置：

```
<目标仓库>/
├── CLAUDE.md                               # Claude Code / Kiro
├── .hermes.md                              # Hermes Agent v0.17.0
├── .github/
│   └── copilot-instructions.md             # GitHub Copilot Workspace
├── .cursor/
│   └── rules/governance.mdc               # Cursor
├── .gitlab/
│   └── merge_request_templates/
│       └── default.md                      # GitLab MR 模板
├── docs/governance/
│   ├── mr-spec.md
│   └── risk-types.md
├── governance.config.yml
└── governance/
    └── ci-snippet.yml
```

---

## 一键安装

### 方式一：本地克隆后安装（推荐）

```bash
# 1. 克隆治理规范仓库（或 submodule 引用）
git clone https://gitlab.example.com/your-org/governance.git /tmp/governance

# 2. 在目标仓库根目录执行
cd /path/to/your-repo
bash /tmp/governance/governance/install.sh .
```

### 方式二：curl 一行安装（需设置 raw URL）

```bash
cd /path/to/your-repo
export GOVERNANCE_SOURCE="https://gitlab.example.com/your-org/governance/-/raw/main/governance"
curl -fsSL "${GOVERNANCE_SOURCE}/install.sh" | bash
```

### 方式三：仓库内自装（本仓库已包含 governance/）

```bash
cd /path/to/this-repo
bash governance/install.sh .
```

脚本幂等，可重入。已存在的文件自动备份为 `*.bak.<时间戳>`，不会静默覆盖。

---

## 安装后操作（3 步）

### 第 1 步：把 MR 模板提交进仓库

```bash
git checkout -b chore/governance-v1
git add .gitlab/ docs/governance/ governance.config.yml governance/ci-snippet.yml
git commit -m "chore: install MR governance v1"
git push -u origin chore/governance-v1
# 然后在 GitLab 上提 MR 合入
```

### 第 2 步：接入 CI

在 `.gitlab-ci.yml` 的 `include:` 段加一行：

```yaml
include:
  - local: '/ci/test.yml'
  - local: '/ci/flow.yml'
  - local: '/governance/ci-snippet.yml'   # ← 新增
```

> v1 的 `governance:risk-scan` 是硬阻断，`governance:mr-validate` 是软警告（不阻断合并）。

### 第 3 步：在 GitLab 确认模板生效

新建 MR 时，描述框右上角 → "选择模板" → 选 `default` 即可自动填充。

---

## v1 软启动说明

| 规则 | v1 模式 | 到期转硬 |
|---|---|---|
| 风险注解（8 类）| 硬阻断，第一天起生效 | — |
| 密钥扫描 | 硬阻断，第一天起生效 | — |
| 测试删除保护 | 硬阻断，第一天起生效 | — |
| MR 描述字段（M1–M4）| 软警告 | `governance.config.yml` 的 `soft_deadline` |

`soft_deadline` 安装时自动设为距今 90 天，修改需要走 MR。

---

## AI agent 使用须知

install.sh 会向四个 AI 工具分别部署指令文件，各工具在会话启动时自动加载：

| Agent 工具 | 指令文件 | 加载时机 |
|---|---|---|
| Claude Code / Kiro | `CLAUDE.md`（仓库根） | 会话启动时自动 |
| Hermes Agent v0.17.0 | `.hermes.md`（仓库根） | 会话启动时自动，优先于 AGENTS.md |
| OpenAI Codex CLI | `AGENTS.md`（仓库根） | 任务启动时自动，同时作为 Hermes fallback |
| GitHub Copilot Workspace | `.github/copilot-instructions.md` | 自动注入 system prompt |
| Cursor | `.cursor/rules/governance.mdc` | Always apply 规则 |

提交代码前，AI agent 必须对照 `docs/governance/risk-types.md` 做自检：

1. 每次编辑源码后，把客观证据（工具/模型/增删行数）追加进 `.governance/ai-evidence.jsonl`。
2. 改了生产代码就写测试，用 `record_test_run.py` 跑（留痕到 `Tested:` trailer）；确无法单测的加 `risk:untested` 注解。
3. diff 里有没有命中 8 类风险模式？
4. 有 → 在该代码上方加 `risk:*` 注解，四个字段缺一不可（type、reason、owner、reviewed）。
5. reason 不能含黑名单词（临时 / 先这样 / 历史原因 / TODO / hack / wip 等）。
6. `reviewed:` 填当天日期，有效期 6 个月，过期会在下次触碰该文件时强制刷新。
7. 提交即可——`AI-Usage` 和 `Tested` trailer 由 git hook 自动写入，**无需手填**。

---

## DeliverHQ 共存

安装脚本自动检测 `DeliverHQ/` 目录。检测到时：

- `governance.config.yml` 自动写入 `deliverhq_integration.enabled: true`
- `records_dirs` 同时包含 `docs/requirements/` 和 `DeliverHQ/change-requests/`
- MR 描述的 `Requirement-ID:` 填 `CR-xxxx` 或 `REQ-xxxx` 均可

两者通过 **commit trailer + `DeliverHQ/evidence-summary.json`** 联动，不互相读取内部目录结构。

---

## 修改规范

| 改什么 | 流程 |
|---|---|
| 新增风险类型 | 改 `docs/governance/risk-types.md` → MR 标题 `governance: add risk type <name>` → CODEOWNERS 审批 |
| 调整 enforcement 参数 | 改 `governance.config.yml` → MR → CODEOWNERS 审批 |
| 延期 soft_deadline | 改 `governance.config.yml` → MR → CODEOWNERS 审批（不能私自改文件） |
| 升级 v2 | 新建 `governance/v2/` 目录，不破坏 v1 |

