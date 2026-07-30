# 03 · 开发者终端配置手册

面向**用 AI agent 编程的开发者**。每人配置一次。

预计耗时：15–20 分钟（取决于装几个工具）。

---

## 你要完成的事

1. 安装并登录你要用的 AI agent 工具（至少一个）
2. 确认工具能自动加载仓库里的规范指令文件
3. 安装 AI-Usage 自动采集 git hook（提交时自动写 trailer，无需手填）
4. 本地验证

---

## 第 1 步：安装 AI agent 工具

按你团队用的工具选择。每个工具读的规范文件不同，但 install.sh 已经把它们都装进仓库了，你只需装好工具本体。

### Claude Code / Kiro

```bash
# 安装（参考官方文档）
npm install -g @anthropic-ai/claude-code
# 或使用公司内部分发渠道

# 登录
claude login
```

读取的规范文件：仓库根 `CLAUDE.md`（自动加载）。

### Hermes Agent v0.17.0

```bash
# 按 Hermes Agent 官方方式安装 v0.17.0
# https://github.com/NousResearch/hermes-agent

# 验证版本
hermes --version   # 应显示 v0.17.0 或对应日期版本
```

读取的规范文件：仓库根 `.hermes.md`（优先），fallback 到 `AGENTS.md` / `CLAUDE.md`。
注意 Hermes 有 `context_file_max_chars` 默认 20000 字符限制，本规范文件控制在限制内。

### OpenAI Codex CLI

```bash
# 安装 Codex CLI（参考 OpenAI 官方）
npm install -g @openai/codex
# 或公司内部渠道

codex login
```

读取的规范文件：仓库根 `AGENTS.md`（自动，从 git root 向下逐级发现），默认大小限制 32 KiB。

### Cursor

下载安装 Cursor 编辑器（https://cursor.com）。

读取的规范文件：`.cursor/rules/governance.mdc`（标记为 always-apply，自动生效）。

### GitHub Copilot Workspace

在支持 Copilot 的 IDE 里登录 GitHub 账号。

读取的规范文件：`.github/copilot-instructions.md`（自动注入 system prompt）。

---

## 第 2 步：确认规范指令被加载

进入已安装规范的业务仓库，验证你的工具确实读到了指令。

### 通用验证方法

在仓库根确认指令文件存在：

```bash
ls -la CLAUDE.md .hermes.md AGENTS.md \
       .github/copilot-instructions.md \
       .cursor/rules/governance.mdc 2>/dev/null
```

### 按工具验证

| 工具 | 验证方式 |
|---|---|
| Claude Code / Kiro | 启动后问它："本仓库的提交规范要求我做什么？" 应能复述风险注解和 MR 字段要求 |
| Hermes Agent | 启动后查看其加载的 context files，应包含 `.hermes.md` |
| Codex CLI | 启动后问规范要求，应引用 `AGENTS.md` 内容 |
| Cursor | 在 Settings → Rules 里应看到 governance 规则为 active |
| Copilot | 让它生成代码时，应主动加风险注解或提醒 MR 字段 |

如果工具没读到，检查：文件是否在 git root、文件名大小写是否正确、工具版本是否支持该文件名。

---

## 第 3 步：安装 AI-Usage 自动采集 git hook

本规范**不让你手填 AI 使用程度**。AI 占比由机器自动统计：agent 开发时把每次编辑的客观证据写入 `.governance/ai-evidence.jsonl`，提交时 hook 调用脚本对照实际 diff 自动算等级、写入 commit trailer。

### 一次性安装 hook（每个仓库一次）

```bash
cd /path/to/your-repo
bash governance/scripts/install-hooks.sh
```

它会：
- 在 `.git/hooks/prepare-commit-msg` 安装钩子，提交时自动追加 `AI-Usage` / `AI-Tools` / `AI-Lines` trailer
- 把 `.governance/ai-evidence.jsonl` 加入 `.gitignore`（证据是会话产物，不入库）

> install.sh 安装规范时，如检测到是 git 仓库会**自动调用**这个脚本，通常你无需手动跑。仅在克隆了新仓库、或 hook 丢失时再手动执行。

### 验证 hook 生效

```bash
# 随便改一个源码文件后, 预览本次提交将写入的 trailer
python governance/scripts/collect_ai_usage.py --staged
```

看到 `判定等级: ...` 和建议 trailer 即正常。真正 `git commit` 时这些行会自动追加到 commit message 末尾。

> **取值含义**：`none`（无 AI）/ `light`（≤20%）/ `medium`（20–60%）/ `heavy`（>60%）/ `used`（补全类工具，程度无法精确测）。等级由脚本按 AI 改动行占比自动算，你不需要也不应该手改。

---

## 第 4 步：配置个人 token（一次性，每台机器做一次）

> GitLab 地址和 project id 维护者已经在 `governance.config.yml` 里配好了，你**只需要配自己的 token**。

Token 是 AgentGate 代替你调 GitLab API 的凭据，相当于你的"API 密码"，每人一个，不能共用，不能提交进仓库。

### 第一步：在 GitLab 生成 token

1. 用浏览器打开公司 GitLab（`https://gitlab.ushaqi.com`），登录
2. 右上角**头像** → **User Settings** → 左侧点 **Access Tokens**
3. 填写：
   - **Token name**：填 `agentgate-mr`
   - **Scopes**：勾选 **`api`**（必须勾，其他不用管）
4. 点 **Create personal access token**
5. 页面顶部出现一串字符（`glpat-xxxxxxxxxxxx`），**立刻复制，关页面就看不到了**

### 第二步：写入环境变量

**Windows（PowerShell）**

按 `Win + X` 打开 PowerShell，运行（把 `粘贴你的token` 换成刚才复制的值）：

```powershell
[Environment]::SetEnvironmentVariable("AGENTGATE_GITLAB_TOKEN", "粘贴你的token", "User")
```

让当前窗口立刻生效（不用重开终端）：

```powershell
$env:AGENTGATE_GITLAB_TOKEN = [Environment]::GetEnvironmentVariable("AGENTGATE_GITLAB_TOKEN", "User")
```

**Linux / macOS（终端）**

```bash
echo 'export AGENTGATE_GITLAB_TOKEN="粘贴你的token"' >> ~/.bashrc
source ~/.bashrc
```

> macOS 默认用 zsh，把 `~/.bashrc` 换成 `~/.zshrc`。

### 第三步：验证

**Windows：**
```powershell
echo $env:AGENTGATE_GITLAB_TOKEN
# 应输出你的 token 值，不为空即正确
```

**Linux / macOS：**
```bash
echo $AGENTGATE_GITLAB_TOKEN
# 应输出你的 token 值，不为空即正确
```

### 第四步：运行预检

进入仓库目录，跑一下预检确认能通：

```bash
python governance/scripts/create_mr.py --gitlab-api --gitlab-preflight
# 输出: [create-mr] GitLab API 预检通过: net/netx
```

预检通过后，以后让 AI agent 提 MR 只需：

```bash
python governance/scripts/create_mr.py --gitlab-api --why "本次改动的背景"
```

> **安全提示**：token 只放环境变量，不能写进任何代码文件或 commit。

---

### 4.5 验证全部配置正确

打开终端，进入仓库目录，运行预检命令：

**Windows PowerShell**：

```powershell
# 先进入仓库目录，例如：
cd C:\Code\netx

# 运行预检
python governance/scripts/create_mr.py --gitlab-api --gitlab-preflight
```

**Linux / macOS**：

```bash
cd ~/code/netx
python governance/scripts/create_mr.py --gitlab-api --gitlab-preflight
```

成功输出：

```
[create-mr] GitLab API 预检通过: net/netx
```

如果报错，对照下表排查：

| 报错 | 原因 | 解决 |
|---|---|---|
| `缺少 AGENTGATE_GITLAB_TOKEN` | token 没写进环境变量 | 重新执行 4.2，确认 `echo $env:AGENTGATE_GITLAB_TOKEN` 有输出 |
| `401 Unauthorized` | token 无效或 scope 没勾 `api` | 回 GitLab 重新生成 token，勾选 `api` |
| `404 Not Found` | project id 填的是路径而不是数字 | 重新执行 4.3，用数字 id |
| `缺少 AGENTGATE_GITLAB_URL` | GitLab 地址没写进环境变量 | 重新执行 4.2 的第二条命令 |

---

预检通过后，以后提 MR 只需：

```bash
python governance/scripts/create_mr.py \
  --gitlab-api \
  --why "这次改动的背景"
```

---

## 第 5 步：本地预检（提交前自查）

在提 MR 前，本地先跑一遍能省去 CI 打回的来回。

### 手动对照风险清单

打开仓库的 `docs/governance/risk-types.md`，对照你的改动检查 8 类风险模式。命中就加注解（格式见该文档）。

### 用 AI agent 自检（推荐）

直接让你的 agent 执行：

```
请对照 docs/governance/risk-types.md 扫描我当前的 git diff，
列出命中的风险模式，并为每一处生成符合格式的 risk: 注解。
```

合规的 agent（已加载规范）会主动完成这一步。

### 等 CI 脚本就位后的本地预跑

> v1 阶段 `scan_risks.py` / `validate_mr.py` 尚在开发。脚本就绪后，可在本地这样预跑：
>
> ```bash
> python governance/scripts/scan_risks.py --diff-base origin/master
> python governance/scripts/validate_mr.py --file MR_DESCRIPTION.md
> ```

---

## 验收清单

- [ ] 至少一个 AI agent 工具已安装并登录
- [ ] 在业务仓库里，工具能读到对应的规范指令文件
- [ ] git commit trailer 模板已配置
- [ ] `AGENTGATE_GITLAB_TOKEN` 已写入环境变量
- [ ] `create_mr.py --gitlab-api --gitlab-preflight` 预检通过
- [ ] 知道在哪查 `docs/governance/risk-types.md`
- [ ] 知道如何让 agent 自检 diff

---

## 下一步

阅读 `04-ai-agent-workflow.md`，掌握每次提交的标准流程。
