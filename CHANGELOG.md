# Changelog

All notable changes to AgentGate will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
