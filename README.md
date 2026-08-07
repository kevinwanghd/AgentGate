# AgentGate · AI 代码治理工具包

> 让 AI agent 自主写代码、自主提交、自主合并，**靠 CI 而非人工**把控质量。  
> 全程自动留痕（AI 用量 / 测试 / 风险注解），5 分钟接入，规则可配。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: GitLab/GitHub](https://img.shields.io/badge/Platform-GitLab%20%7C%20GitHub-orange)]()
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-green)]()

---

## 📖 完整文档

| 文档 | 说明 |
|---|---|
| **[INSTALL.md](INSTALL.md)** | 本地安装指南 (Windows/Linux/macOS + GitLab/GitHub/本地) |
| **[USER_GUIDE.md](USER_GUIDE.md)** | 使用手册 (开发者工作流 + 配置 + 高级用法 + 管理员指南) |

**5 分钟快速上手** → 直接看 [USER_GUIDE.md 第 1 节](USER_GUIDE.md#5-分钟快速上手)

---

## ⚡ 快速安装

### GitLab 项目(推荐)

```bash
# 1. 克隆 AgentGate 到本地
git clone https://github.com/kevinwanghd/AgentGate.git ~/agentgate

# 2. 进入你的 GitLab 项目
cd /path/to/your-gitlab-project

# 3. 运行安装(只装 Claude 指令,减少根目录文件)
bash ~/agentgate/install.sh . --agents claude

# 4. 接入 GitLab CI
echo 'include:
  - local: "/governance/ci-snippet.yml"' >> .gitlab-ci.yml

# 5. 提交推送
git add . && git commit -m "chore: 接入 AgentGate 治理" && git push
```

**可选 `--agents` 参数**(控制装哪些 AI 指令文件,减少根目录散落):
- `--agents all` — 装所有(Claude + Copilot + Cursor + Hermes,默认)
- `--agents claude` — 只装 Claude(根目录只有 `CLAUDE.md`)
- `--agents none` — 不装任何 AI 指令文件

### GitHub 项目(中央自动更新)

GitHub 仓库推荐使用 thin 模式: 业务仓库只保留一个很薄的 workflow 入口,
真正的门禁脚本从 AgentGate 中央仓库读取。

```bash
bash ~/agentgate/install.sh . \
  --platform github \
  --mode thin \
  --agentgate-repo kevinwanghd/AgentGate \
  --agentgate-ref github-stable \
  --agents claude
```

安装后业务仓库会调用:

```yaml
uses: kevinwanghd/AgentGate/.github/workflows/agentgate.yml@github-stable
```

以后只要推进 AgentGate 的 `github-stable` 分支, 所有引用该分支的 GitHub 仓库
会在下一次 PR 检查时自动使用新版门禁。

### GitLab 项目发布线

GitLab 不跟随 `github-stable`。GitLab 仓库继续使用本地 pinned 安装, 或由平台管理员
单独引用 `gitlab-stable` 发布线:

```yaml
include:
  - project: 'platform/AgentGate'
    ref: gitlab-stable
    file: '/gitlab/ci-snippet.yml'
```

因此更新 GitHub 自动更新线不会影响 GitLab 上的其他仓库。

---

## 🔍 这套工具做什么

### 12 项自动检查(CI 跑)

| 检查 | 做什么 | 拦什么 |
|---|---|---|
| **risk-scan** | 扫描 8 类内置风险模式 + 语言专属规则包 + 自定义规则；warn 命中写 Job Summary | 硬编码 ID/密钥、SQL 拼接、认证绕过等 |
| **secret-scan** | gitleaks 检测密钥泄露 | 私钥、API token、数据库连接串 |
| **test-check** | 验证测试覆盖 | 改了生产代码但没测试痕迹 |
| **mr-validate** | 校验 MR 描述格式（**必须使用中文**）；大 PR 写拆分建议到 Job Summary | 缺背景/变更内容/自测确认段落，或未使用中文撰写 |
| **go-test** | 受影响 Go 包（含反向依赖一跳）跑 `go test`；**无 go.mod 自动跳过** | 直接/间接受影响包测试失败 |
| **flutter-test** | 递归查找 `pubspec.yaml`，在各 Flutter 子目录跑 `flutter test`；**无 pubspec.yaml 自动跳过** | Flutter 测试失败 |
| **python-test** | 递归查找 `requirements.txt/pyproject.toml/setup.py`，跑 `pytest`；**无标记文件自动跳过** | Python 测试失败 |
| **node-test** | 递归查找 `package.json`，跑 `npm test`；**无 package.json 自动跳过** | Node.js/TypeScript 测试失败 |
| **java-test** | 递归查找 `pom.xml/build.gradle`，跑 `mvn test` 或 `gradle test`；**无标记文件自动跳过** | Java 测试失败 |
| **dotnet-test** | 递归查找 `*.sln/*.csproj`，跑 `dotnet test`；**无标记文件自动跳过** | .NET/C# 测试失败 |
| **rust-test** | 查找 `Cargo.toml`，跑 `cargo test`（根目录优先含 workspace）；**无标记文件自动跳过** | Rust 测试失败 |
| **selftest** | 工具自检 | 确保脚本本身没 bug(仅 AgentGate 仓库) |

所有语言测试 job 支持 monorepo：递归查找最深 3 层，找到多个子目录时逐一运行，任意一个失败则整个 job 失败。不适用的语言自动 skip，不阻断合并。

### 本地自动化

- ✅ **提交时自动盖 AI-Usage trailer**(读 AI 证据,算占比,写进 commit message)
- ✅ **自动盖 Tested trailer**(记录测试运行结果)
- ✅ **本地预检**(提交前手动跑同样的扫描)

### CI 驱动自动合并

AgentGate 默认启用 CI 驱动的自动合并策略：

- `scripts/gate_decision.py` 汇总当前提交的必需检查，生成 GateResult v2；
- GateResult 绑定 source SHA、target SHA 和 policy SHA，旧提交证据不能复用；
- 普通 LOW/MEDIUM 变更在所有 required checks 通过后可由 Merge Bot 自动合并；
- 治理配置、CI、门禁脚本和权限相关路径自动升级为 CRITICAL，保持 PR 打开并等待人工批准；
- 本地 `Tested:` trailer 和 PR 描述只作审计上下文，不作为自动合并依据。

GitLab 接入后会新增 `governance:gate-decision` 与 `governance:auto-merge`：

- `gate-decision` 只生成 `gate-result.json` 和 `GATE_MERGE_ACTION`，不调用平台 API；
- `auto-merge` 仅在 `AUTO_MERGE`、同项目 MR、当前 source SHA 匹配时调用 GitLab Merge API；
- GitLab 自动合并 token 必须放在受保护/Masked 变量 `GOVERNANCE_MERGE_BOT_TOKEN` 中；
- Merge Bot token 应来自独立 Bot/Project Access Token，开发 agent 不应拥有主干写权限或合并权限。

### 旧版 GitLab 的 MR 描述门禁

GitLab 11.x 的 branch pipeline 通常拿不到 MR 描述。AgentGate 不再要求每位开发者配置 Personal Access Token，而是校验分支内的 `.agentgate/mr-description.md`：

```bash
python governance/scripts/agentgate.py mr prepare \
  --why "<用中文说明任务背景>"

git add .agentgate/mr-description.md
git commit -m "docs: prepare merge request description"
```

CI 会确认该文件由当前分支修改过，并通过 GitLab API 读取真实 MR 描述后按正常 MR 规范校验。现代 GitLab 的 MR pipeline 仍直接校验真实 MR 描述。手工创建 MR 时，可将清单正文作为描述起点；真实 MR 描述不需要和清单逐字一致，但必须保留必填模块内容。

### 留痕与审计

每次提交自动记录:
```
AI-Usage: heavy
AI-Tools: claude-code
AI-Models: opus-4.8
AI-Lines: 23/25
Tested: pass (12/12)
```

风险代码必须加注解(留审计痕迹):
```csharp
// risk:auth-bypass reason:"管理后台内网访问已通过IP白名单隔离" owner:@security-team reviewed:2026-06-30
if (req.Headers["X-Internal"] == "true") return true;
```

---

## 🎯 为什么需要这个

**场景**:团队用 AI(Claude/Cursor/Copilot)写代码,速度快但质量怎么保证?

**传统方案**:人工 code review → 慢、主观、漏检  
**AgentGate 方案**:机器自动检查 → 快、客观、全覆盖

| 问题 | AgentGate 怎么解决 |
|---|---|
| AI 写了不安全的代码 | risk-scan 拦住,要求加注解说明 |
| 不知道哪些代码是 AI 写的 | 自动记录 AI-Usage,可统计、可审计 |
| 改了代码没跑测试 | test-check 要求测试痕迹 |
| 提交了密钥到仓库 | secret-scan 立刻拦截 |
| MR 描述写得太随意 | mr-validate 强制写背景/变更/自测，**且必须使用中文** |
| 同一个仓库反复踩同一个坑 | `governance/lessons/repository.yml` 记录仓库本地 lessons，所有 agent 开始修改前必须读取 |

---

## 🛠️ 核心脚本

AgentGate 包含 15 个 Python 脚本(在 `scripts/` 目录):

| 脚本 | 功能 |
|---|---|
| `scan_risks.py` | 风险代码扫描(8 类内置 + 自定义规则；warn 命中写 Job Summary) |
| `check_tested.py` | 测试覆盖检查 |
| `validate_mr.py` | MR 描述校验；大 PR 写拆分建议到 Job Summary |
| `gate_decision.py` | 生成 GateResult v2，决定自动合并/等待审批/阻断 |
| `run_affected_tests.py` | Go 受影响包测试 + 反向依赖一跳扩展 |
| `collect_ai_usage.py` | AI 用量统计(读证据,算占比,盖 trailer) |
| `record_test_run.py` | 记录测试运行(盖 Tested trailer) |
| `create_mr.py` | 自动生成 MR(从 commit 提取信息)，支持生成分支 MR 描述清单 |
| `agentgate.py` | 统一 CLI 入口；`mr prepare` 准备清单，`mr create` 提交 MR |
| `gitlab_mr_compat.py` | GitLab CE 11.x 兼容校验：读取真实 MR 描述，并用当前分支清单校验新鲜度 |
| `gitlab_controller.py` | GitLab 11.4 外部 Controller 最小版，检查 Bot/API/保护分支/目标策略并创建 MR |
| `evidence_bundle.py` | 生成 Evidence Plan/Bundle v2，校验证据与 source/target/merge/policy/profile 绑定 |
| `risk_merge_decision.py` | 基于证据包做风险分级、审批校验、自动合并动作和审计记录 |
| `report_expired.py` | 过期注解周报(找 90 天未复查的风险注解) |
| `validate_lessons.py` | 校验 hard lessons 是否绑定可执行治理检查 |
| `install-hooks.sh` | 安装 git hook |
| `selftest.sh` | 工具自检(48 个用例) |

---

## 🚀 工作流程

### 1. 开发者:AI 写代码

用 Claude/Cursor/Copilot 写代码,工具自动留证据(`.governance/ai-evidence.jsonl`)。

### 2. 提交时:自动盖 trailer

```bash
git commit -m "feat: 加支付功能"
# hook 自动追加:
# AI-Usage: heavy (23/25)
# Tested: pass (12/12)
```

### 3. 发 MR:CI 自动检查

推送后,GitLab/GitHub CI 跑 4 个 job:
- ❌ **risk-scan FAIL** → 检测到硬编码 ID,要求加注解
- ✅ **secret-scan PASS**
- ❌ **test-check FAIL** → 没测试覆盖
- ✅ **mr-validate PASS**

### 4. 修复:加注解 + 补测试

```csharp
// risk:magic-id reason:"合法机器人账号白名单" owner:@team reviewed:2026-06-30
if (user.Id == "bot_12345") return true;
```

补测试后再推,CI 全绿 → 可以合并。

---

## 📊 配置示例

`governance/config.yml`（完整说明见 [USER_GUIDE.md](USER_GUIDE.md#配置参考)）:

```yaml
metadata:
  enforcement: soft       # soft(只警告) / hard(拦合并)
  soft_deadline: 90       # 90 天后自动切 hard

risk_annotations:
  enforcement: soft
  pattern_includes:
    - governance/patterns/go.yml   # 可选: 接入 Go 专项风险规则包
  registered_types:
    - auth-bypass
    - magic-id
    - my-unsafe-api       # 公司自定义类型
  reviewed_max_age_days: 90
  reason_blacklist: [临时, hack]
  custom_patterns:         # 公司专属扫描规则
    - type: my-unsafe-api
      regex: 'UnsafeAPI\s*\('
      desc: "禁用 UnsafeAPI"

testing:
  enforcement: soft
  exclude_paths: ["*.md", "docs/"]

auto_merge:
  enabled: true
  critical_approvals: 1   # 改了 CI/受保护路径时需要的人工审批数
  required_checks:        # 所有项目统一配置，语言特定 job 不适用时自动 skip
    - risk-scan
    - secret-scan
    - mr-validate
    - test-check
    - go-test
  language_checks:        # 这些 job 未触发（missing）或返回 skip 时均视为通过
    - go-test
    - flutter-test
    - python-test
    - node-test
    - java-test
    - dotnet-test
    - rust-test
```

---

## 🎓 实战案例

- **AgentGate 自己** — 用自己管自己(dogfooding),5 个 job + 分支保护,硬门禁

---

## 📂 安装后的文件结构

```
你的项目/
├── governance/                # 治理文件集中目录
│   ├── config.yml             # 配置(风险规则/测试要求/门禁强度)
│   ├── ci-snippet.yml         # GitLab CI 片段
│   ├── mr-spec.md             # MR 规范说明
│   ├── risk-types.md          # 风险类型清单
│   ├── lessons/               # 仓库记忆: 全局 lessons + 本仓库 repository.yml
│   │   ├── repository.yml     # 本仓库本地 lessons, 安装时创建且不覆盖
│   │   └── ...
│   ├── patterns/              # 语言专属风险规则包(可选 include)
│   │   ├── go.yml             # Go (10条 warn 规则)
│   │   ├── csharp.yml         # C# / .NET (6条)
│   │   ├── python.yml         # Python (8条)
│   │   ├── javascript.yml     # JavaScript/TypeScript (8条)
│   │   ├── java.yml           # Java (10条)
│   │   └── dart.yml           # Dart/Flutter (10条)
│   └── scripts/               # 治理检查与 GateResult 脚本
│       ├── scan_risks.py
│       ├── check_tested.py
│       └── ...
├── .gitlab/
│   └── merge_request_templates/
│       └── default.md         # MR 模板
├── CLAUDE.md                  # AI 指令(可选,用 --agents 控制)
└── .git/hooks/
    └── prepare-commit-msg     # 提交时自动盖 trailer
```

**减少根目录散落**:用 `--agents claude` 只装 Claude,根目录只有 1 个 `CLAUDE.md`,不装 `.hermes.md` / `AGENTS.md` / `.cursor/` / `.github/copilot-instructions.md`。

**仓库 lessons**:安装器会创建 `governance/lessons/repository.yml`。任何 agent 在这个仓库踩过的重复错误、门禁失败或本地约束误解，都应记录到这里；能用测试/扫描/门禁验证的写 `enforcement: hard` 并绑定可执行检查，暂时只能提醒的写 `soft`。所有 agent 指令入口都会要求开工前读取 `governance/lessons/*.yml`。

---

## 🔗 相关资源

- **文档**:[INSTALL.md](INSTALL.md) | [USER_GUIDE.md](USER_GUIDE.md)
- **问题反馈**:[GitHub Issues](https://github.com/kevinwanghd/AgentGate/issues)
- **版本**:v1.3.0 (2026-07-22)

---

## 📜 License

MIT License - 详见 [LICENSE](LICENSE)

---

## 🙏 致谢
