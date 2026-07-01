# AgentGate 本地安装指南

本文档帮助你在**本地开发机**和**仓库**上安装 AgentGate MR 治理工具包。

---

## 环境要求

| 组件 | 说明 |
|---|---|
| **操作系统** | Windows / Linux / macOS |
| **Git** | 2.0+ (用于 hook 和 diff 分析) |
| **Python** | 3.8+ (运行扫描脚本) |
| **Bash** | Windows 用 Git Bash,Linux/macOS 自带 |

### Windows 用户注意

Windows 需要通过 **Git Bash** 运行安装命令:
1. 安装 Git for Windows:https://git-scm.com/download/win (自带 Git Bash)
2. 安装 Python:https://www.python.org/downloads/ (**勾选 "Add Python to PATH"**)
3. 开始菜单搜 "Git Bash",在里面运行后续命令

---

## 三种安装场景

| 场景 | 说明 | 适合 |
|---|---|---|
| **A. GitLab 项目** | 装到 GitLab 仓库,接入 GitLab CI | GitLab 团队(推荐,原生支持) |
| **B. GitHub 项目** | 装到 GitHub 仓库,接入 GitHub Actions | GitHub 团队 |
| **C. 本地开发环境** | 只装 git hook,不接 CI | 个人开发、离线环境 |

---

## 场景 A:GitLab 项目(推荐)

### 1. 克隆 AgentGate 到本地

**一次性操作**——把 AgentGate 拉到本地固定位置,后续所有项目共用:

```bash
# Linux/macOS/Git Bash
git clone https://github.com/kevinwanghd/AgentGate.git ~/agentgate
```

### 2. 进入你的 GitLab 项目

```bash
cd /path/to/your-gitlab-project   # Linux/macOS
cd /d/Code/你的项目名              # Windows Git Bash (盘符小写,路径用/)
```

### 3. 运行安装脚本

```bash
bash ~/agentgate/install.sh .
```

**脚本会自动:**
- 在 `governance/` 下生成 9 个检查脚本
- 生成 `governance/config.yml`(配置文件)
- 生成 `governance/ci-snippet.yml`(GitLab CI 片段)
- 生成 `.gitlab/merge_request_templates/default.md`(MR 模板)
- 写入 `CLAUDE.md` 等 AI 指令文件
- 安装 git hook(提交时自动盖 AI-Usage/Tested trailer)

完成后看到:
```
============================================================
 AgentGate 安装完成
============================================================
```

### 4. 接入 GitLab CI

打开你的 `.gitlab-ci.yml`,在开头加一行:

```yaml
include:
  - local: '/governance/ci-snippet.yml'
```

如果项目还没有 `.gitlab-ci.yml`,创建一个:

```yaml
include:
  - local: '/governance/ci-snippet.yml'

# 你的其他 CI job 照常写在下面
```

### 5. 提交并推送

```bash
git checkout -b chore/governance
git add .
git commit -m "chore: 接入 AgentGate MR 治理规范

## 背景 - 引入 AI 代码自动化质量检查。
## 变更内容 - 4 个 CI job:风险扫描/密钥检测/测试覆盖/MR 校验。
## 自测确认 - 本地 selftest 通过。
## 风险与回滚 - 低风险纯新增,revert 即可。"

git push -u origin chore/governance
```

然后在 GitLab 网页上发 Merge Request 合入 `main/master`。

### 6. 配置分支保护(推荐)

合并完成后,让门禁真正强制生效:

1. **Settings → Repository → Protected branches**
   - 选择 `main` 或 `master`
   - 勾选 **"Developers cannot push"**(强制走 MR)
   - 勾选 **"Pipelines must succeed"**(CI 过才能合)

2. **Settings → Merge requests**
   - 勾选 **"Pipelines must succeed"**

配置后,任何红了的 MR 都无法合入——门禁生效。

---

## 场景 B:GitHub 项目

GitHub 项目需要手动翻译 CI 配置(AgentGate 的 `ci-snippet.yml` 是 GitLab 格式)。

### 1-3. 同上(克隆 AgentGate、进项目、跑 install.sh)

```bash
git clone https://github.com/kevinwanghd/AgentGate.git ~/agentgate
cd /path/to/your-github-project
bash ~/agentgate/install.sh .
```

### 4. 手动创建 GitHub Actions workflow

`install.sh` 不会自动生成 GitHub 的 workflow。参考 [UseGEO 的部署](https://github.com/kevinwanghd/UseGEO/blob/master/.github/workflows/governance.yml),创建 `.github/workflows/governance.yml`:

```yaml
name: MR Governance
on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  risk-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - run: |
          python governance/scripts/scan_risks.py \
            --diff-base ${{ github.event.pull_request.base.sha }} \
            --config governance/config.yml

  secret-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          docker run --rm -v "$PWD:/scan" zricethezav/gitleaks:latest \
            detect --source /scan --verbose --no-git

  test-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - run: |
          python governance/scripts/check_tested.py \
            --diff-base ${{ github.event.pull_request.base.sha }} \
            --config governance/config.yml

  mr-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          python governance/scripts/validate_mr.py \
            --body "${{ github.event.pull_request.body }}" \
            --config governance/config.yml
```

### 5. 提交推送,配分支保护

```bash
git checkout -b chore/governance
git add .
git commit -m "chore: 接入 AgentGate 治理规范"
git push -u origin chore/governance
```

发 PR 合入后,配置分支保护:
- **Settings → Branches → Add rule**
- Branch name pattern:`main`
- 勾选 **Require status checks**,选中 4 个 job

---

## 场景 C:本地开发环境(只装 hook)

不接 CI,只在本地提交时自动盖 AI-Usage/Tested trailer。

### 1-2. 克隆 AgentGate,进项目

```bash
git clone https://github.com/kevinwanghd/AgentGate.git ~/agentgate
cd /path/to/your-project
```

### 3. 只装 hook(不生成 CI 配置)

```bash
bash ~/agentgate/scripts/install-hooks.sh
```

这会:
- 安装 `.git/hooks/prepare-commit-msg`(提交时自动算 AI-Usage 并盖 trailer)
- 把 `.governance/` 加进 `.gitignore`

### 4. 验证

改个文件提交,看 commit message 末尾是否有:

```
AI-Usage: heavy
AI-Tools: claude-code
AI-Models: opus-4.8
AI-Lines: 23/25
Tested: none
```

有 → 成功。

---

## 验证安装

### 本地验证

```bash
# 1. Python 依赖
pip install pyyaml   # 或 pip3 install pyyaml

# 2. 跑自测(40+ 个用例)
cd ~/agentgate
bash scripts/selftest.sh
# 应输出: 通过 42 / 失败 0

# 3. 手动扫描测试
cd /path/to/your-project
python ~/agentgate/scripts/scan_risks.py --diff-base HEAD~1 --config governance/config.yml
```

### CI 验证(GitLab/GitHub)

提交一个小改动,发 MR/PR,看是否触发 4 个 job:
- `risk-scan`
- `secret-scan`
- `test-check`
- `mr-validate`

4 个都跑且有结果 → CI 接入成功。

---

## 常见问题

### Q1:Windows 上 `bash: command not found`

**原因**:没装 Git Bash,或在 PowerShell/CMD 里跑。  
**解决**:开始菜单搜 "Git Bash",在那里面跑命令。

### Q2:`python: command not found`

**原因**:Python 没装或没加 PATH。  
**解决**:
1. 装 Python:https://www.python.org/downloads/ (**勾 "Add to PATH"**)
2. 重开 Git Bash
3. 再试 `python --version`

### Q3:提交时没有自动盖 AI-Usage trailer

**原因**:hook 没装或 Python 路径不对。  
**诊断**:
```bash
ls -la .git/hooks/prepare-commit-msg   # 有这个文件吗?
cat .git/hooks/prepare-commit-msg      # 内容对吗?
python --version                       # Python 能找到吗?
```

**修复**:重新跑 `bash ~/agentgate/scripts/install-hooks.sh`。

### Q4:GitLab CI 报 `scripts/xxx.py not found`

**原因**:`ci-snippet.yml` 里的路径不对,或 include 写错了。  
**解决**:
1. 确认 `.gitlab-ci.yml` 有 `include: - local: '/governance/ci-snippet.yml'`
2. 确认 `governance/scripts/` 目录存在且有 9 个 `.py` 脚本

### Q5:所有 CI job 都红,报 `no module named yaml`

**原因**:CI 环境没装 PyYAML。  
**解决**:在 `ci-snippet.yml` 的 `before_script` 里加:
```yaml
before_script:
  - pip install pyyaml
```

### Q6:部署 MR 本身被 risk-scan 拦住(11 处"扫描器扫自己")

**原因**:自包含形态(脚本在仓库里),扫描器识别自己的规则定义为"风险模式"。  
**解决**:
- **方案 A**:第一个部署 MR 用 admin 权限强制合入(之后就没这个问题)
- **方案 B**:改用中心化形态(脚本不放仓库,CI 从 AgentGate 拉)

---

## 下一步

安装完成后,阅读 [使用手册](USER_GUIDE.md) 了解:
- 开发者日常工作流
- 如何加风险注解
- 如何调整配置
- 如何自定义扫描规则

或直接开始开发——提交代码时系统会自动检查并给出反馈。
