# 02 · 仓库接入手册（事业部技术负责人）

面向**管理具体仓库的人**——你们事业部的技术负责人。

**你管几个仓库，就对每个仓库做一遍。** 单个仓库约 15 分钟。

本手册分两部分：
- **A. 装文件**（在终端跑 install.sh）
- **B. 配仓库设置**（在 GitLab 网页点几下）

---

## 前提

- 平台管理员已完成 `01`（建好了中心 governance 仓库），并给了你两个地址：
  - 克隆地址：`https://gitlab.example.com/platform/governance.git`
  - raw URL：`https://gitlab.example.com/platform/governance/-/raw/master`
- 你对目标业务仓库有 **Maintainer** 权限（配分支保护需要）。

---

# A. 装文件

## A1. 装的是哪些文件

install.sh 会往你的业务仓库写入这些文件（这就是"安装"的全部内容）：

| 写入路径 | 是什么 | 给谁用 |
|---|---|---|
| `.gitlab/merge_request_templates/default.md` | MR 模板 | GitLab 创建 MR 时自动加载 |
| `docs/governance/mr-spec.md` | MR 规范正文 | 人查阅 |
| `docs/governance/risk-types.md` | 8 类风险注解目录 | AI agent 提交前对照 |
| `governance.config.yml` | 门禁参数（软/硬、阈值） | CI 读取 |
| `governance/ci-snippet.yml` | CI 集成片段 | 接入 `.gitlab-ci.yml` |
| `CLAUDE.md` | 指令文件 | Claude Code / Kiro |
| `.hermes.md` | 指令文件 | Hermes Agent |
| `AGENTS.md` | 指令文件 | OpenAI Codex（+ Hermes fallback） |
| `.github/copilot-instructions.md` | 指令文件 | GitHub Copilot |
| `.cursor/rules/governance.mdc` | 指令文件 | Cursor |

> install.sh 是**幂等**的：已存在的文件先备份成 `*.bak.<时间戳>` 再写；`governance.config.yml` 已存在则直接跳过；`CLAUDE.md` 已存在则**追加**而非覆盖。重复跑安全。

## A2. 怎么执行（任选一种方式）

### 方式 A：克隆后安装（推荐，最稳）

```bash
# 1. 把中心规范仓库克隆到临时目录（每台机器只需一次）
git clone https://gitlab.example.com/platform/governance.git /tmp/governance

# 2. 进入你要接入的业务仓库根目录
cd /path/to/your-repo

# 3. 执行安装，参数 "." 表示装到当前目录
bash /tmp/governance/install.sh .
```

### 方式 B：curl 一行安装（需要 raw URL 可匿名访问）

```bash
# 1. 进入业务仓库根目录
cd /path/to/your-repo

# 2. 指定中心规范仓库的 raw URL 前缀
export GOVERNANCE_SOURCE="https://gitlab.example.com/platform/governance/-/raw/master"

# 3. 拉取并执行
curl -fsSL "${GOVERNANCE_SOURCE}/install.sh" | bash
```

> 方式 B 的原理：脚本通过 `GOVERNANCE_SOURCE` 这个 raw URL 去拉取模板和规范文件。如果你们内网 raw URL 需要登录，用方式 A。

### 安装时你会看到

脚本会逐行打印写入了哪些文件，结尾有一段总结，包含：
- 已安装文件清单
- `soft_deadline`（= 安装日 + 90 天）
- 是否检测到 DeliverHQ

## A3. 验证装好了

```bash
# 在业务仓库根目录执行
ls .gitlab/merge_request_templates/default.md   # MR 模板
ls CLAUDE.md .hermes.md AGENTS.md               # AI 指令文件
grep soft_deadline governance.config.yml        # 看到一个 90 天后的日期
```

三条都有输出就装好了。

---

# B. 配仓库设置（GitLab 网页操作）

装完文件还不够——还要在 GitLab 上配好门禁，否则文件只是躺在仓库里，没人强制执行。以下都在你这个业务仓库的网页设置里，**你作为 Maintainer 就能配**。

## B1. 分支保护：禁止直接 push master

网页路径：`你的仓库 → Settings → Repository → Protected branches`

| 配置项 | 设成 | 为什么 |
|---|---|---|
| Branch | `master`（或你们主干名） | |
| Allowed to merge | `Maintainers` | 只有维护者能合 |
| Allowed to push and merge | `No one` | **禁止直接 push 主干，强制走 MR** |
| Allowed to force push | `Off` | 防止历史被改写 |

这是整套门禁的地基。没有它，谁都能绕过 MR 直接改主干。

## B2. 开启"流水线必须通过"

网页路径：`你的仓库 → Settings → Merge requests`

| 配置项 | 设成 | 为什么 |
|---|---|---|
| Merge method | `Fast-forward` 或 `Squash commits` | 保持主干整洁 |
| **Pipelines must succeed** | ✅ **开启** | **核心**：CI 红了（含风险扫描）就合不进来 |
| All threads must be resolved | ✅ 开启 | 评论必须处理完 |
| Approvals required | `0`（v1 默认） | AI 自主合并，不卡人工；敏感文件靠 B3 的 CODEOWNERS 兜底 |

> 这一项让"AI 自主合并 + CI 把关"成立：AI 可以自己点合并，但只有 CI 全绿才合得进去。

## B3. CODEOWNERS：锁住规范和敏感文件

普通业务代码让 AI 自主合并，但规范文件、CI、密钥相关文件不能被随意改弱。用 CODEOWNERS 把它们锁给你（或治理小组）。

在业务仓库根目录新建文件 `CODEOWNERS`（注意没有扩展名）：

```
# 治理规范文件，改动需技术负责人审批
governance.config.yml             @你的gitlab用户名
docs/governance/                  @你的gitlab用户名
.gitlab/merge_request_templates/  @你的gitlab用户名

# CI 与部署
.gitlab-ci.yml                    @你的gitlab用户名
ci/                               @你的gitlab用户名

# AI agent 指令文件
CLAUDE.md                         @你的gitlab用户名
.hermes.md                        @你的gitlab用户名
AGENTS.md                         @你的gitlab用户名
.github/copilot-instructions.md   @你的gitlab用户名
.cursor/rules/                    @你的gitlab用户名
```

> 把 `@你的gitlab用户名` 换成你的账号，或一个 group 如 `@yourteam/leads`。

然后开启 code owner 审批：
`Settings → Merge requests → Approval rules → 勾选 "Require approval from code owners"`

效果：只有**触及上述文件**的 MR 才需要你 approve，普通业务 MR 不受影响、AI 照常自主合并。

## B4. 接入 CI

打开业务仓库的 `.gitlab-ci.yml`，在 `include:` 段加一行。

本仓库（X.Flow）现有 `.gitlab-ci.yml` 已经有 `include` 段（包含 `/ci/test.yml`、`/ci/flow.yml`），直接加：

```yaml
include:
  - '/ci/test.yml'
  - '/ci/flow.yml'
  - local: '/governance/ci-snippet.yml'     # ← 新增这行
```

> 如果平台管理员发布了中心 CI 模板，改用远程引用更好（升级时只改一处）：
> ```yaml
> include:
>   - project: 'platform/governance'
>     ref: master
>     file: '/ci/governance-ci.yml'
> ```

---

# C. 提交 MR 合入

把装好的文件提交，合进 master：

```bash
git checkout -b chore/governance-v1
git add .gitlab/ docs/governance/ governance.config.yml governance/ \
        CLAUDE.md .hermes.md AGENTS.md \
        .github/copilot-instructions.md .cursor/rules/ CODEOWNERS
git commit -m "chore: 接入 MR 治理规范 v1"
git push -u origin chore/governance-v1
```

然后在 GitLab 网页为该分支创建 MR、合入 master。这个 MR 本身就能用刚装好的模板填，算第一次演练。

---

## 单仓库完成清单

- [ ] A：install.sh 跑完，三条验证命令都有输出
- [ ] B1：master 已 protected，禁止直接 push
- [ ] B2：已开启 "Pipelines must succeed"
- [ ] B3：CODEOWNERS 已建并开启 code owner 审批
- [ ] B4：`.gitlab-ci.yml` 已 include governance CI
- [ ] C：规范文件已提 MR 合入 master

**每个你管的仓库重复 A–C 一遍。**

---

## 常见问题

**Q：A（装文件）和 B（配网页）的顺序？**
先 A 后 B 都行，但 B4（接 CI）依赖 A 生成的 `ci-snippet.yml`，所以 CI 那步要在装文件之后。

**Q：我管 5 个仓库，A1 的克隆要做 5 次吗？**
不用。`git clone .../governance /tmp/governance` 在一台机器上做一次即可，之后对 5 个仓库分别 `cd` 进去跑 `bash /tmp/governance/install.sh .`。

**Q：已有 CLAUDE.md 会被覆盖吗？**
不会，追加一个 `governance-v1-begin/end` 区块，保留原内容。

**Q：团队不用 Cursor，能不装它的文件吗？**
装完删掉 `.cursor/rules/governance.mdc`、提交时不 `git add` 它即可，不影响其他部分。

**Q：v1 的 CI 现在真能拦住风险代码吗？**
能。`scan_risks.py`（风险扫描，硬门禁）和 `validate_mr.py`（MR 校验，软门禁）已实现并随 install.sh 装入 `governance/scripts/`，`ci-snippet.yml` 已调用它们。装好后可本地验证：`bash governance/scripts/selftest.sh`（10 个用例）。CI 镜像需有 python3 + pyyaml。详见 `05-ci-reference.md`。

---

## 下一步

通知你团队的开发者读 `03-developer-terminal-setup.md`，配置各自终端的 AI 工具。

零基础、第一次提 MR 的新人，直接发 `06-newcomer-guide.md` 给他们——一篇就能上手，不用先读规范细节。
