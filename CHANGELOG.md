# Changelog

All notable changes to AgentGate will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed

- **自举门禁 `governance.yml` 策略改从目标分支读取** — 此前 `POLICY_SHA` 取自 PR head 且 `--config` 读 PR 工作区，PR 可清空 `protected_paths`/降低 `critical_approvals` 自授权 `AUTO_MERGE`；现与 `agentgate.yml`/GitLab 一致走 `--target-ref`（目标分支无配置时用内置默认值）。
- **`governance.yml` 补工作流级最小权限与按 PR `concurrency`** — 无覆盖 job（含在 PR 代码上执行脚本的 scan/selftest）只拿只读 token；同 PR 新推送取消旧运行，旧判定不能再为新提交开启自动合并。
- **`risk-scan`/`secret-scan`/`yaml-validate` 在 `governance.yml` 无条件运行** — 属必查项，path-filter 跳过会被 gate 按 missing/skip 阻断纯文档 PR；扫描开销极小。
- **`yaml-validate` 纳入必查项并进入 gate 证据** — 此前 yaml-validate 失败不进 evidence，改 `.yml`+`.py` 的 PR 可在语法错误的情况下 AUTO_MERGE。
- **auto-merge 复核 PR head SHA** — `governance.yml`/`agentgate.yml` 开启自动合并前比对当前 head 与判定时 head，防止旧运行为未过门禁的新提交"上膛"；`agentgate.yml` 补 draft/fork 守卫与 `GOVERNANCE_GO_TEST_TIMEOUT` 变量传递。
- **调用方模板权限修正** — `templates/github_agentgate_workflow.yml` 工作流级 `contents: read` → `write`：可复用工作流 job 级 `contents: write` 受 caller 上限约束，此前薄接入仓库 auto-merge 必报 "Resource not accessible by integration"。
- **GitLab `governance:test-bypass` 移入 test stage 并声明 `dependencies`** — GitLab 同 stage job 互相看不到 artifact，此前读不到 test-check 的 `.governance/test-evidence.jsonl`，必误报硬阻断。

### Fixed

- **`check_job.py` 重命名绕过** — `git diff -M` 的 R 状态被丢弃，`git mv` + 改内容可整体绕过 test-bypass 门禁；改 `--no-renames` 且兼容 R 状态取新路径。
- **`check_job.py` 漏识别 pytest 布局** — 候选不含 `tests/test_foo.py`，Python 仓库的"有测试但无执行记录"检测形同虚设。
- **`check_job.py` covers 语义** — `covers` 记录生产文件却与测试候选比对，真跑了测试也 FAIL；候选并入生产文件本身。
- **`pending_writer.py` snippet 行号偏移一行** — `@@` 头行被计入行号，指纹退化为 `路径:行号`；并限制 hunk 匹配不跨文件。
- **`pending_writer.py` 原子写** — merge 路径直接截断重写，中断丢整条 lesson；改临时文件 + `os.replace`。
- **`scan_risks.py` `++` 内容行误判文件头** — 新增行以 `++` 开头（如 `++ counter;`）被当 `+++ b/` 头，同文件后续行全部不扫；文件头判定改为精确匹配 `a/`/`b/`/`/dev/null`。
- **`scan_risks.py` 测试文件正则过宽** — IGNORECASE 的 `Tests?\.[a-z]+$` 把 `latest.cs`/`contest.java` 误判为测试文件，其中所有 block 规则被跳过；改大小写敏感匹配（与 check_tested 一致）。
- **`scan_secrets.py` `++` 前缀行漏扫** — `not startswith("+++")` 把内容以 `++` 开头的新增行整行丢弃，密钥可藏其中；同样补扫 `base...HEAD` 总差异覆盖 evil merge 引入的密钥，按 `(path,line,规则)` 去重。
- **`gate_decision.py` `--target-ref` 忽略 `--config`** — 目标分支策略路径硬编码 `governance.config.yml`，自定义 config-path 时 `git show` 失败门禁永远 ERROR。
- **`create_mr.py` Windows 引号命令** — `shlex.split(posix=False)` 保留引号导致带引号 preflight 命令必然 FileNotFoundError；剥掉首尾成对引号。
- **`create_mr.py` GitLab API 读超时** — 裸 `TimeoutError` 不是 `URLError` 子类，未捕获时直接 traceback 绕过回退。
- **`governance_metrics.py`/`aggregate_pending.py` 坏数据崩溃** — pending YAML 根节点非 mapping 时 AttributeError；同 fingerprint 记录缺 `status` 时 `sorted()` 抛 TypeError；均改为跳过/归一 pending。
- **文档漂移** — `05-ci-reference.md` 修正 secret-scan 实现描述（内置 `scan_secrets.py`，非 gitleaks 镜像）、`missing` 的 ERROR 语义、`GIT_DEPTH`、补齐 test-bypass/lessons-validate/mr-validate-compat job 表。

- **GitLab 11.x MR 描述门禁不再依赖个人 PAT** — branch pipeline 优先校验当前分支提交的 `.agentgate/mr-description.md`，并要求该文件相对目标分支已更新；GitLab API 降为可选回退。
- **新增 `agentgate.py mr prepare`** — 自动生成、校验并写入 MR 描述清单，供旧版 GitLab CI 和手工创建 MR 共同使用。
- **目标分支流水线自动跳过 MR 描述校验** — `master/main` 自身没有对应 MR，不再因缺少 MR 描述误报失败。
- **API 回退改为显式最小权限** — 默认不访问项目接口；启用时只读取专用 `AGENTGATE_GITLAB_READ_TOKEN`，不再接受个人 token、`PRIVATE_TOKEN` 或 Merge Bot token。
- **描述证据标注真实性** — 结果文件写入 `actual_mr_verified` 和描述摘要，区分“真实 MR 已验证”与“仅分支清单已验证”。
- **GitLab CI 镜像改为 internal-only 策略** — 治理和语言测试 job 全部通过 `GOVERNANCE_*_IMAGE` 变量选择镜像，默认不引用 Docker Hub、MCR、GHCR 等公网运行时镜像；安装器新增 `--ci-*-image` 参数用于写入业务仓库内部预构建镜像。

### Fixed

- **GitHub gate 改从目标分支读取策略** — 此前读 PR 工作区的 `governance.config.yml`，PR 可改写自身被评判的策略；目标分支无配置时使用内置默认值。
- **目标分支策略合并默认值** — 配置缺 `protected_paths` 等字段时不再使保护失效；required checks 不再回退到 PR 侧 evidence 自带的 check 名。
- **`check_tested --ci-mode` 旧证据绕过** — evidence 文件非空但无当前代码状态记录时同样硬阻断。
- **删除残留的内置 `sensitive-log` block 规则** — #76 未实际移除，导致全语言误伤；该规则由 `patterns/<lang>.yml` 以 warn 模式提供。
- **安装器补齐 `check_job.py`、`pending_lessons_schema.py`** — GitLab CI 引用但未安装，导致 `governance:lessons-validate` 失败。
- **pending lessons 飞轮** — `pending_writer` import 即 TypeError 与出现次数丢失；指标/聚合改读 `review.*`（并兼容混合时区）；`lessons_review` 前缀歧义时拒绝操作、未知 status 不再崩溃。
- **git 调用** — `scan_risks`/`check_tested` 使用 `core.quotepath=off`（非 ASCII 文件名不再被跳过），`scan_secrets` 加 `--no-color`。
- **gitignore 条目** — hook 安装脚本只忽略 `.governance/*.jsonl`、`sessions/`、`reports/`，不再整目录忽略导致 `pending-lessons/` 无法入库；`enable-local.sh` 兼容 worktree / `core.hooksPath`。
- **GitHub 调用模板** 增加按 PR 的 `concurrency` 取消旧运行。
- **pending lessons 统一为 YAML** — `lessons_review` 改读写 YAML；`pending_lessons_schema.py` 改为委托 `validate_pending.py` 的兼容入口；删除无调用方的 `governance_mirror.py`、`governance_aggregate.py`。
- **fingerprint v2** — 保留关键字与字面量占位符，不同结构的违规不再撞指纹。
- **路径 glob 语义统一** — 7 处独立实现统一为 `governance_common.path_matches`（gitignore 风格、大小写敏感）；`**/auth/**` 现在也匹配根目录 `auth/`。
- **profiles** — 头部 schema 统一；安装器不再写入 CI 不产出的 `flutter-analyze`/`bazel-test`。
- **GitHub gate-decision** — 判定 `FAIL`/`ERROR` 时 job 失败（此前始终绿色），`WAITING_APPROVAL` 保持通过并提示。

---

## [1.3.0] - 2026-07-22

### Added

- **Go 受影响包测试 + 反向依赖扩展** — `run_affected_tests.py` 通过 `go list -json` 构建反向依赖图, 改动 pkg/A 时自动扩展并测试所有 importer (1-hop BFS); go list 失败时宽容降级为只测直接改动包。新增 `--no-reverse-deps` 标志供 Bazel / 大型 monorepo 用。
- **行内 `scan:ignore reason:"..."` 豁免** — 在命中行同行或上一行注释 `scan:ignore reason:"..."` (reason ≥ 5 字符) 即可精确豁免单行, 替代整目录白名单。自指误报问题根治方案。
- **GitHub Actions `go-test` job** — `.github/workflows/governance.yml` 补充 `go-test` job, 与 GitLab CI 的 `governance:go-test` 功能对等; 非 Go 仓库 (无 go.mod) 自动跳过; 支持 `GOVERNANCE_SKIP_GO_TEST` / `GOVERNANCE_GO_TEST_TIMEOUT` 变量。
- **Go 风险规则包 `patterns/go.yml`** — 10 条 Go 专属 warn 模式: `swallowed-error`、`context-background`、`sensitive-log`、`go-sql-concat`、`go-hardcoded-secret`、`go-cmd-inject`、`go-tls-skip-verify`、`go-ssrf`、`go-panic-in-handler`、`go-weak-random`。warn 模式观察期不阻断 CI, 命中写入 Job Summary 可见。
- **`install.sh` 补全 Go 支持** — 新安装的消费仓库自动获得 `run_affected_tests.py`、`patterns/go.yml` 及 ci-snippet 中的 `governance:go-test` job。
- **warn 违规写入 Job Summary** — `scan_risks.py` 将 warn 命中汇总写入 `$GITHUB_STEP_SUMMARY` (⚠️ 表格), block 违规写 ❌ 表格, 全绿写 ✅ 摘要。
- **PR edited 触发** — GitHub Actions workflow 新增 `edited` 和 `reopened` 事件类型, 修改 PR 描述后立即重跑门禁。
- **AI-Usage 改为可选** — `DEFAULT_CONFIG.mandatory_fields` 移除 `ai_usage`; 消费仓库默认不强制该字段, 团队可在 `governance.config.yml` 里显式加回。

### Fixed

- **Go 代码误报系统性修复** — `swallowed-exception`、`skipped-test`、`env-hardcode`、`todo-no-context` 等规则增加扩展名过滤, 不再误报 Go 文件。
- **测试文件豁免** — Go `_test.go` / Python `test_*.py` 等测试文件不再触发 `skipped-test` 规则 (测试文件里的 skip 是合法的)。
- **Go 规则边界加固** — 多行 `exec.Command` / HTTP 动态 URL 可被识别; SQL 拼接覆盖 `fmt.Sprintf`、`+` 和跨行拼接; 硬编码敏感值支持 Go raw string 和结构体字段; `math/rand` 收窄到 token/nonce/验证码等安全上下文; `panic` 描述与非测试 Go 文件扫描范围保持一致。

---

## [1.2.1] - 2026-07-01

### Fixed

- **扫描器自指误报 (关键修复)** — 自包含形态的业务仓库中, 当 MR 改动到 `governance/scripts/` 下的脚本副本时, scan_risks.py 会扫到脚本里的风险模式**字面示例**(如 swallowed-exception 规则注释里的 `catch { }`、黑名单词、ObjectId 样例), 误判为命中风险并硬拦, 阻碍提交。
  - **根因**: 扫描器脚本本身合理地、大量包含风险模式的字面量(它们是规则的"家"), 但默认 `scan_exclude_paths` 未排除 `governance/scripts/`, 导致扫描器扫到自己。
  - **修复**: 默认豁免路径加入 `**/governance/scripts/**` 和 `governance/scripts/**` — 扫描器不扫自己。同步到 scan_risks.py DEFAULT_CONFIG、install.sh 生成的 config。
  - **回归测试**: selftest 新增自指豁免用例(共 50 个)。

---

## [1.2.0] - 2026-07-01

### Fixed — 多人协作阻碍系统性修复

经系统性审计(12 个协作场景), 确认并修复 8 个会阻碍正常多人协作的门禁问题。核心原则: **exit 1 只留给开发者本次真正新写的风险逻辑, 不为 git 重吐的他人历史代码背锅**。

- **[P0] 生成/引入代码路径豁免** — scan_risks.py 新增 `scan_exclude_paths` 配置, 支持 `**` 跨目录 glob。vendor/node_modules/*.pb.go/*.generated.* 等整文件跳过风险扫描 (之前该字段被静默忽略)。
- **[P0] 默认门禁软化** — risk_annotations.enforcement 默认 `hard→soft`。revert/cherry-pick 恢复他人历史风险行无法从 diff 层干净区分, 默认只警告, 团队显式 opt-in hard 才硬拦。结构性缺陷(缺注解/字段错/类型未注册/黑名单词)在 hard 下仍拦。
- **[P1] rename 不误拦** — check_tested.py 用 `--name-status -M` 检测, 重命名/移动(R/C 状态)文件不要求补测试。
- **[P1] squash 历史塌陷修复** — 用二点端点 diff 交集过滤, 目标分支 squash 后未变的历史文件不计入你的改动。
- **[P1] rebase/squash 不丢 Tested trailer** — hook 在证据缺失时不写 `Tested:none` 覆盖历史; read_tested_trailer 取全范围最强信号(任一 pass 即 pass)。
- **[P1] 重排版不误报** — scan_risks/check_tested 的 git diff 加 `-w`, 重缩进/换行包裹触碰他人风险行不再误判为新增。
- **[P2] 注解过期降级软提醒** — 继承来的他人过期注解只提示不阻断(exit 0), 不因日期年龄卡死协作。

### Changed

- 消费仓库默认配置(install.sh): risk_annotations 默认 soft + 内置 scan_exclude_paths。
- AgentGate 自身保持 hard (dogfooding 展示严格模式), 并加 scan_exclude_paths。

### Testing

- selftest 新增 5 个协作场景回归用例 + 更新 4 个既有用例反映软化默认, 共 49 个全过。

### 推行结论

修完后可在公司层面推行: 守住"新增未注解风险代码"的真实防线, 杜绝"替同事/第三方代码背锅"的协作阻碍。

---

## [1.1.2] - 2026-07-01

### Documentation

- **新增 diff-base bug 复盘文档** — `docs/postmortem-diff-base-2026-07-01.md`，完整记录多人协作误拦 bug 的现象、根因分析、修复方案、验证方法和经验教训。

---

## [1.1.1] - 2026-07-01

### Fixed

- **多人协作 diff-base 误拦 (关键修复)** — 修复多人协作时的误拦 bug: 当你 `git pull`/merge 了同事已合入目标分支的代码后再提交, CI 会把同事的改动也算进"你要负责的 diff", 要求你为没写过的代码提供测试痕迹, 导致被误拦。
  - **根因**: CI 传给扫描脚本的 diff-base 是 MR/PR **创建时的旧快照** (`base.sha` / `CI_MERGE_REQUEST_DIFF_BASE_SHA`), 不随目标分支更新。用旧 base 做三点 diff, 后来 merge 进来的他人改动会被算进来。
  - **修复**: CI 改用**目标分支最新 tip** (`origin/<base.ref>` / `origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME`) 作 base, 先 `git fetch` 拉最新再算。三点 diff 自动取 merge-base, 正确排除已在目标分支的他人改动。
  - **影响**: GitHub workflow 4 个 job + GitLab ci-snippet 4 个 job 全部修正。
  - **回归测试**: selftest 新增 2 个多人协作用例 (共 44 个)。

---

## [1.1.0] - 2026-07-01

### Added

- **公司自定义扫描规则 (`custom_patterns`)** — 允许在 `governance/config.yml` 中配置公司专属的风险检测规则，无需修改代码即可扩展扫描能力。支持正则表达式，无效规则自动跳过并警告。[#3]
  
  ```yaml
  risk_annotations:
    custom_patterns:
      - type: my-unsafe-api
        regex: 'UnsafeAPI\s*\('
        desc: "禁用内部 UnsafeAPI"
  ```

- **`--agents` 参数** — `install.sh` 新增可选参数，允许按需安装 AI 指令文件，显著减少目标项目根目录的文件散落。[#5]
  - `--agents all` (默认): 安装所有 AI 工具指令
  - `--agents claude`: 只安装 Claude 指令（根目录仅增加 1 个文件）
  - `--agents copilot/cursor/hermes`: 按需安装
  - `--agents none`: 不安装任何 AI 指令文件

- **本地一键启用脚本 (`enable-local.sh`)** — 快速在本地启用 AgentGate hook 和预检功能。[#2]

- **完整文档** — 新增两份中文文档，覆盖安装、使用、配置、高级用法全流程：[#4]
  - `INSTALL.md`: 本地安装指南（Windows/Linux/macOS + GitLab/GitHub/本地三种场景）
  - `USER_GUIDE.md`: 使用手册（5分钟快速上手 + 开发者工作流 + 配置参考 + CI 详解 + 高级用法 + 管理员指南）

### Changed

- **目录结构整理** — 配置文件和文档收拢到 `governance/` 目录，提升组织性：[#3]
  - `governance.config.yml` → `governance/config.yml`
  - `docs/governance/` → `governance/`
  - 脚本的 `load_config()` 向后兼容新旧两个位置

- **README 全面改写** — 清晰的项目定位、功能说明、工作流程图、配置示例、安装后文件结构可视化。[#5]

- **版本号统一** — `README.md`、`install.sh`、`governance.config.yml` 三处版本号统一更新为 v1.1.0。[#6]

### Fixed

- **`collect_ai_usage.py` 文件扩展名支持** — 修复只识别 `.py/.js/.java` 的问题，扩展到 `.sh/.yml/.json/.ts/.tsx/.jsx/.go/.rs/.rb/.php/.cs/.cpp/.c/.h` 等常见编程语言和配置文件。[#2]

### Removed

- **README 中移除内部项目信息** — 删除 UseGEO 实战案例引用，使 README 更通用。[#7]

---

## [1.0.0] - 2026-06-30

### Added

- **核心检查脚本**（9 个）:
  - `scan_risks.py` — 风险代码扫描（8 类内置模式）
  - `check_tested.py` — 测试覆盖检查
  - `validate_mr.py` — MR 描述校验
  - `collect_ai_usage.py` — AI 用量统计
  - `record_test_run.py` — 记录测试运行
  - `create_mr.py` — 自动生成 MR
  - `report_expired.py` — 过期注解周报
  - `install-hooks.sh` — 安装 git hook
  - `selftest.sh` — 工具自检（40 个用例）

- **GitLab CI 集成** — `ci-snippet.yml` 提供 4 个 job 模板:
  - `risk-scan` — 风险代码扫描
  - `secret-scan` — 密钥泄露检测（gitleaks）
  - `test-check` — 测试覆盖检查
  - `mr-validate` — MR 描述校验

- **AI 指令文件** — 支持 5 种 AI 工具:
  - Claude Code / Kiro (`CLAUDE.md`)
  - GitHub Copilot (`.github/copilot-instructions.md`)
  - Cursor (`.cursor/rules/governance.mdc`)
  - Hermes Agent (`.hermes.md`)
  - OpenAI Codex CLI (`AGENTS.md`)

- **本地自动化**:
  - git hook 自动盖 AI-Usage/Tested trailer
  - 提交前可手动预检

- **配置系统**:
  - `governance.config.yml` 支持软/硬启动、注解过期时间、黑名单词配置
  - MR 模板（`.gitlab/merge_request_templates/default.md`）

- **风险注解机制**:
  - 8 类内置风险模式（auth-bypass/magic-id/sql-concat/hardcoded-crypto 等）
  - 注解格式：`risk:<type> reason:"..." owner:@team reviewed:YYYY-MM-DD`
  - 默认 180 天过期

### Documentation

- `docs/` 目录完整操作手册（00-05 共 6 份文档）
- `mr-spec.md` / `risk-types.md` 规范说明

---

## 版本说明

### v1.1.0 主要改进

相比 v1.0.0，v1.1.0 带来三大核心改进：

1. **可定制性** — `custom_patterns` 让每个公司能配自己的风险规则
2. **干净部署** — `--agents` 参数大幅减少目标项目根目录文件数量
3. **文档完善** — 新增 INSTALL.md / USER_GUIDE.md，覆盖安装到使用的全流程

### 升级指南 (v1.0.0 → v1.1.0)

已部署 v1.0.0 的项目**无需任何操作**，v1.1.0 向后兼容：
- 脚本自动查找新旧两个 config 位置
- 未使用 `--agents` 时行为与 v1.0.0 一致
- 新功能 `custom_patterns` 可选配置

如需使用新功能：
1. **自定义扫描规则** — 编辑 `governance/config.yml` 加 `custom_patterns`
2. **减少根目录文件** — 下次重装时用 `--agents claude`

---

## 贡献

AgentGate 基于 AI 辅助开发完成。欢迎通过 [GitHub Issues](https://github.com/kevinwanghd/AgentGate/issues) 反馈问题和建议。
