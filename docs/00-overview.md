# MR 治理规范 v1 · 总览

本目录是全公司统一的 **MR（Merge Request）治理规范**，目标是：

> 让 AI agent 自主写代码、自主提交、自主合并，**靠 CI 而非人工**把控质量；同时第一版不对现有开发流程造成强制阻碍。

---

## 这套规范由什么组成

```
governance/
├── README.md                       # 快速上手（5 分钟）
├── install.sh                      # 一键安装脚本
├── mr-spec.md                      # MR 规范正文（人读）
├── risk-types.md                   # 8 类风险注解目录（AI agent 提交前必读）
│
├── templates/
│   └── merge_request_default.md    # GitLab MR 模板源文件
│
├── agent-instructions/             # 各 AI 工具的指令文件源
│   ├── CLAUDE.md                   #   → Claude Code / Kiro
│   ├── hermes-instructions.md      #   → Hermes Agent / Codex
│   ├── copilot-instructions.md     #   → GitHub Copilot
│   └── cursor-rules.mdc            #   → Cursor
│
└── docs/                           # ← 你正在读的操作手册目录
    ├── 00-overview.md              # 本文件
    ├── 01-gitlab-admin-setup.md    # 平台管理员：建中心规范仓库（全公司一次）
    ├── 02-repo-onboarding.md       # 事业部技术负责人：装文件 + 配仓库设置（每仓库一次）
    ├── 03-developer-terminal-setup.md  # 开发者：终端 + AI 工具配置
    ├── 04-ai-agent-workflow.md     # AI agent：每次提交前的标准流程
    ├── 05-ci-reference.md          # CI 门禁：各 job 的行为与排查
    └── 06-newcomer-guide.md        # 零基础新人：第一次提 MR 怎么做
```

---

## 两层 enforcement 模型

这是理解整套规范的关键。规则分两类，强度不同：

| 层 | 包含 | v1 行为 | 何时变硬 |
|---|---|---|---|
| **硬阻断层** | 风险注解、密钥扫描、测试删除保护 | **第一天起就拦** | — |
| **软提示层** | MR 描述字段（背景/变更/AI 声明/自测） | **只警告，不拦** | `soft_deadline`（默认 90 天后）自动转硬 |

这个设计直接对应两个核心要求：

- **不阻碍现有流程**：软层让大家有 90 天缓冲适应 MR 描述规范，期间不会因为漏填字段被挡住。
- **风险代码必须说明**：硬层从第一天就强制，凡是 AI 或人写出"看起来可疑"的代码（如硬编码用户 ID 免登录），必须加结构化注解解释清楚，否则 CI 直接拒合。

---

## 谁该读哪份文档

| 你的角色 | 是谁 | 读这些 | 频率 |
|---|---|---|---|
| **GitLab 平台管理员** | 全公司平台组 / DevOps | `01-gitlab-admin-setup.md` | 全公司**一次** |
| **事业部技术负责人** | 管理若干仓库的你 | `02-repo-onboarding.md` | **每个仓库一次** |
| **开发者** | 用 AI agent 编程的人 | `03-developer-terminal-setup.md` + `04-ai-agent-workflow.md` | 每人一次 |
| **零基础新人** | 没写过程序、第一次提 MR | `06-newcomer-guide.md` | 入职看一次 |
| **想了解 CI 为什么拦我** | 任何人 | `05-ci-reference.md` | 按需查 |
| **想了解规则细节** | 任何人 | `../mr-spec.md` + `../risk-types.md` | 按需查 |

> 角色边界：**平台管理员全公司只做一次**——建一个集中的规范仓库供大家拉取。**分支保护、MR 审批、CODEOWNERS、CI 接入都是单个仓库的设置**，由事业部技术负责人作为该仓库 Maintainer 自己完成，不需要平台管理员介入。你管几个仓库，就把 `02` 的流程做几遍。

---

## 端到端安装路径（鸟瞰图）

```
┌─────────────────────────────────────────────────────────┐
│ 阶段 1：平台管理员（全公司一次）                           │
│  · 建一个集中的 governance 规范仓库                       │
│  · push 这套文件, 记下克隆地址 + raw URL                  │
│  → 详见 01-gitlab-admin-setup.md                         │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 阶段 2：事业部技术负责人（每个仓库一次）                   │
│  A. 装文件: bash install.sh .                            │
│  B. 配仓库: 分支保护 + Pipelines must succeed + CODEOWNERS│
│  B. 接 CI: .gitlab-ci.yml include 片段                   │
│  C. 提 MR 合入                                            │
│  → 详见 02-repo-onboarding.md                            │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 阶段 3：开发者终端（每人一次）                             │
│  · 安装并配置 AI agent 工具                               │
│  · 配置 git commit trailer 模板                          │
│  → 详见 03-developer-terminal-setup.md                   │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 阶段 4：日常开发（每次提交）                               │
│  · AI agent 自动加载规范 → 自检 → 加注解 → 提 MR          │
│  · CI 门禁自动把关                                        │
│  → 详见 04-ai-agent-workflow.md + 05-ci-reference.md     │
└─────────────────────────────────────────────────────────┘
```

---

## 与 DeliverHQ 共存

如果某仓库已经用 DeliverHQ：install.sh 会自动检测 `DeliverHQ/` 目录并开启共存模式。两者通过 commit trailer + `evidence-summary.json` 弱联动，**不互相依赖、不互相读内部结构**。详见各仓库安装手册的"DeliverHQ 共存"章节。

---

## 版本与变更

- 当前版本：**v1.0.0**
- 不在 v1 范围内的（留待 v2）：强制 Requirement-ID、覆盖率阈值、双 agent 评审、Roslyn analyzer、自动回滚策略。
- 修改规范本身（新增风险类型、调整阈值、延期 deadline）都要走 MR + CODEOWNERS 审批，见 `01-gitlab-admin-setup.md` 的"治理规范自身的变更管理"。
