# 01 · 一次性中心配置（平台管理员）

面向 **GitLab 平台管理员 / DevOps**。

**整个公司只做一次。** 做的事只有一件：建一个集中的 governance 规范仓库，让所有事业部能从这里拉取安装脚本和规范文件。

> 分支保护、MR 审批、CODEOWNERS 这些都是**单个仓库的设置**，由各仓库的负责人（事业部技术负责人）自己配，不在本文档范围。见 `02-repo-onboarding.md`。

预计耗时：10 分钟。

---

## 这一步要装的"文件"

就是把当前这个 `governance/` 目录原样放进一个新的 GitLab 仓库。文件清单：

```
governance/
├── install.sh                          ← 一键安装脚本
├── README.md
├── mr-spec.md                          ← MR 规范正文
├── risk-types.md                       ← 8 类风险注解目录
├── templates/
│   └── merge_request_default.md        ← MR 模板源
├── agent-instructions/                 ← 5 个 AI 工具的指令源
│   ├── CLAUDE.md
│   ├── hermes-instructions.md
│   ├── copilot-instructions.md
│   └── cursor-rules.mdc
└── docs/                               ← 操作手册（你正在读的）
    ├── 00-overview.md
    ├── 01-gitlab-admin-setup.md
    ├── 02-repo-onboarding.md
    ├── 03-developer-terminal-setup.md
    ├── 04-ai-agent-workflow.md
    └── 05-ci-reference.md
```

---

## 操作步骤（照敲）

### 1. 在 GitLab 上新建一个空项目

GitLab 网页：`左上角 + → New project/repository → Create blank project`

- Project name：`governance`
- Project slug：`governance`
- 所属 group：选一个全公司都能访问的 group，例如 `platform`
- Visibility：`Internal`（内网所有登录用户可读）—— 这样各仓库才能拉取
- **不要**勾选 "Initialize repository with a README"

创建后得到地址，例如：`https://gitlab.example.com/platform/governance.git`

### 2. 把 governance 文件推上去

在你本地，把当前的 `governance/` 目录内容推到新仓库：

```bash
# 假设当前 governance/ 目录在 /data/workspace/SelfAutomaticAd/SelfAutomaticAd/governance
cd /data/workspace/SelfAutomaticAd/SelfAutomaticAd/governance

# 初始化并推送到新建的空仓库
git init
git add .
git commit -m "feat: MR 治理规范 v1 初始版本"
git branch -M master
git remote add origin https://gitlab.example.com/platform/governance.git
git push -u origin master
```

推完后，在 GitLab 网页上能看到这些文件就成功了。

### 3. 记下两个地址，发给各事业部技术负责人

这两个地址是各仓库安装时要用的：

**克隆地址**（方式 A 用）：
```
https://gitlab.example.com/platform/governance.git
```

**raw URL 前缀**（方式 B 的 curl 安装用）：
```
https://gitlab.example.com/platform/governance/-/raw/master
```

> 验证 raw URL 可用：
> ```bash
> curl -fsSL "https://gitlab.example.com/platform/governance/-/raw/master/install.sh" | head -5
> ```
> 能看到脚本开头的 `#!/usr/bin/env bash` 就说明内网可访问。如果返回 401/需要登录，改用方式 A（克隆）或给 raw URL 配一个只读 token。

---

## （可选）发布中心 CI 模板

中心模板 `governance/ci/governance-ci.yml` 已就绪。各仓库用 `include:project` 远程引用后，升级时只需在 governance 仓库更新一个文件，所有接入仓库无需改动。

**发布步骤：**

1. 确认文件已在 governance 仓库：`governance/ci/governance-ci.yml`
2. 各业务仓库的 `.gitlab-ci.yml` 改为远程引用（替换本地 `ci-snippet.yml` 引用）：

```yaml
include:
  - project: 'your-group/governance'   # governance 仓库路径
    ref: main
    file: '/governance/ci/governance-ci.yml'
```

3. 可通过 CI/CD Variables 覆盖默认值（项目设置 → Variables）：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `GOVERNANCE_CONFIG_PATH` | governance.config.yml 路径 | `governance/governance.config.yml` |
| `GOVERNANCE_REPORT_OUTPUT` | 周报输出路径 | `governance/reports/expired-annotations.md` |

> v1 首发阶段可跳过——各仓库先用 install.sh 生成的本地 `ci-snippet.yml`，待稳定后再切中心引用。两种方式的 job 行为完全相同。

---

## 完成标志

- [ ] governance 仓库已建，Visibility = Internal
- [ ] `git push` 成功，网页能看到所有文件
- [ ] 克隆地址和 raw URL 已记录
- [ ] raw URL 用 curl 验证可访问（或确认改用克隆方式）
- [ ] 已把这两个地址 + `02-repo-onboarding.md` 发给各事业部技术负责人

到此中心配置完成。剩下的全部是各仓库自己的事。
