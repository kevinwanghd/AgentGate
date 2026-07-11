#!/usr/bin/env bash
#
# MR 治理规范 v1 一键安装脚本
#
# 用法:
#   curl -fsSL <RAW_URL>/governance/install.sh | bash
#   或
#   bash governance/install.sh [目标仓库路径]
#
# 默认目标仓库 = 当前目录。脚本可重入: 已存在的文件会备份成 *.bak.<时间戳>。
#
set -euo pipefail

# ---------- 参数 ----------
TARGET_DIR="$PWD"
AGENTS="all"   # 默认装所有 AI 指令文件
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agents) AGENTS="$2"; shift 2 ;;
    --agents=*) AGENTS="${1#*=}"; shift ;;
    *) TARGET_DIR="$1"; shift ;;
  esac
done
SOURCE_BASE="${GOVERNANCE_SOURCE:-}"
VERSION="v1.2.1"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

# 90 天 soft_deadline 默认值
SOFT_DEADLINE="$(date -u -d "+90 days" +%Y-%m-%d 2>/dev/null || date -u -v+90d +%Y-%m-%d)"

# ---------- 工具函数 ----------
log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[err]\033[0m %s\n' "$*" >&2; }

backup_if_exists() {
  local f="$1"
  if [[ -e "$f" ]]; then
    local bak="${f}.bak.${TIMESTAMP}"
    cp -a "$f" "$bak"
    warn "已备份原文件: $bak"
  fi
}

write_file() {
  local rel="$1"; shift
  local abs="${TARGET_DIR}/${rel}"
  mkdir -p "$(dirname "$abs")"
  backup_if_exists "$abs"
  cat > "$abs"
  ok "写入 $rel"
}

fetch_or_local() {
  # 优先用本地 SOURCE_DIR, 否则从 SOURCE_BASE 拉取
  local rel="$1"
  if [[ -n "${SOURCE_DIR:-}" && -f "${SOURCE_DIR}/${rel}" ]]; then
    cat "${SOURCE_DIR}/${rel}"
  elif [[ -n "$SOURCE_BASE" ]]; then
    curl -fsSL "${SOURCE_BASE}/${rel}"
  else
    err "无法定位源文件 $rel: 请设置 SOURCE_DIR 或 GOVERNANCE_SOURCE"
    exit 1
  fi
}

# ---------- 前置检查 ----------
log "MR 治理规范 ${VERSION} 安装到: ${TARGET_DIR}"

if [[ ! -d "${TARGET_DIR}/.git" ]]; then
  warn "${TARGET_DIR} 不是 git 仓库根目录, 仍继续安装但 CI 集成可能失效。"
fi

# ---------- 自动检测 SOURCE_DIR ----------
# 如果脚本是从仓库内 governance/ 目录运行, 直接复用本地文件
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/mr-spec.md" && -f "${SCRIPT_DIR}/risk-types.md" ]]; then
  SOURCE_DIR="$SCRIPT_DIR"
  log "检测到本地源: ${SOURCE_DIR}"
fi

# ---------- DeliverHQ 共存检测 ----------
DELIVERHQ_INTEGRATION="false"
if [[ -d "${TARGET_DIR}/DeliverHQ" ]]; then
  DELIVERHQ_INTEGRATION="true"
  ok "检测到 DeliverHQ/ 目录, 自动启用共存模式"
fi

# ---------- 1. MR 模板 ----------
log "安装 MR 模板 -> .gitlab/merge_request_templates/default.md"
fetch_or_local "templates/merge_request_default.md" \
  | write_file ".gitlab/merge_request_templates/default.md"

# ---------- 2. 规范文档 ----------
log "安装规范文档 -> docs/governance/"
fetch_or_local "mr-spec.md"    | write_file "docs/governance/mr-spec.md"
fetch_or_local "risk-types.md" | write_file "docs/governance/risk-types.md"

# ---------- 3. AI Agent 指令文件 ----------
# --agents 选项: all(全部) / claude / copilot / cursor / hermes / none
case "$AGENTS" in
  all|claude|copilot|cursor|hermes)
    log "安装 AI agent 指令文件 (--agents=$AGENTS)"
    ;;
  none)
    log "跳过 AI agent 指令文件 (--agents=none)"
    ;;
  *)
    err "无效的 --agents 值: $AGENTS (可选: all/claude/copilot/cursor/hermes/none)"
    exit 1
    ;;
esac

# Claude Code / Kiro
if [[ "$AGENTS" == "all" || "$AGENTS" == "claude" ]]; then
  if [[ -e "${TARGET_DIR}/CLAUDE.md" ]]; then
    warn "CLAUDE.md 已存在, 追加治理规范 section 而非覆盖"
    {
      printf '\n\n---\n<!-- governance-v1-begin -->\n'
      fetch_or_local "agent-instructions/CLAUDE.md"
      printf '\n<!-- governance-v1-end -->\n'
    } >> "${TARGET_DIR}/CLAUDE.md"
    ok "追加 governance 规范到 CLAUDE.md"
  else
    fetch_or_local "agent-instructions/CLAUDE.md" | write_file "CLAUDE.md"
  fi
fi

# GitHub Copilot
if [[ "$AGENTS" == "all" || "$AGENTS" == "copilot" ]]; then
  fetch_or_local "agent-instructions/copilot-instructions.md" \
    | write_file ".github/copilot-instructions.md"
fi

# Cursor
if [[ "$AGENTS" == "all" || "$AGENTS" == "cursor" ]]; then
  fetch_or_local "agent-instructions/cursor-rules.mdc" \
    | write_file ".cursor/rules/governance.mdc"
fi

# Hermes Agent
if [[ "$AGENTS" == "all" || "$AGENTS" == "hermes" ]]; then
  fetch_or_local "agent-instructions/hermes-instructions.md" \
    | write_file ".hermes.md"
  # Hermes fallback: AGENTS.md
  fetch_or_local "agent-instructions/hermes-instructions.md" \
    | write_file "AGENTS.md"
fi

# ---------- 3. governance.config.yml ----------
log "生成 governance.config.yml"
CONFIG_PATH="${TARGET_DIR}/governance.config.yml"
if [[ -e "$CONFIG_PATH" ]]; then
  warn "governance.config.yml 已存在, 跳过 (如需重置, 先手动删除)"
else
  cat > "$CONFIG_PATH" <<EOF
# MR 治理规范配置 v1
version: ${VERSION}

metadata:
  enforcement: soft           # v1 软启动, 缺字段仅警告
  soft_deadline: ${SOFT_DEADLINE}   # 到期自动转 hard, 距今最多 90 天
  mandatory_fields:
    - background          # ## 背景
    - changes             # ## 变更内容
    - ai_usage            # AI-Usage 字段
    - self_test           # ## 自测确认

risk_annotations:
  enforcement: soft           # 默认软启动(只警告); 团队稳定后显式改 hard
  reviewed_max_age_days: 180  # 6 个月
  # 路径豁免: 生成/引入/第三方代码不扫 (开发者不为这些代码负责)
  scan_exclude_paths:
    - "**/governance/scripts/**"   # 扫描器自身不扫自己(含风险模式字面示例)
    - "governance/scripts/**"
    - "**/vendor/**"
    - "**/node_modules/**"
    - "**/third_party/**"
    - "**/*_pb2.py"
    - "**/*_pb2_grpc.py"
    - "**/*.pb.go"
    - "**/*.generated.*"
    - "**/gen/**"
    - "**/dist/**"
    - "**/build/**"
    - "**/migrations/**"
    - "**/*.min.js"
    - "**/*.min.css"
  registered_types:
    - auth-bypass
    - magic-id
    - swallowed-exception
    - suppressed-warning
    - skipped-test
    - time-bypass
    - env-hardcode
    - todo-no-context
    - test-removal
    - untested
  reason_blacklist:
    - 临时
    - 先这样
    - 历史原因
    - TODO
    - 待确认
    - quick fix
    - temp
    - wip
    - hack
    - for now

large_change:
  line_threshold: 500
  excluded_paths:
    - "*.lock"
    - "*.Designer.cs"
    - "migrations/**"
    - "**/*.generated.*"
  sensitive_paths:
    - "ci/"
    - "CODEOWNERS"
    - "charts*/"
    - "*secret*"
    - ".gitlab-ci.yml"
  schema_paths:
    - "*.sql"
    - "migrations/**"
    - "*.proto"

testing:
  enforcement: soft           # v1 软启动: 未测代码仅警告; soft_deadline 后转硬
  soft_deadline: ${SOFT_DEADLINE}
  untested_max_age_days: 180  # risk:untested 注解有效期, 同风险注解
  exclude_paths:              # 整目录/模式免测试检查 (DTO/迁移/生成代码/启动引导)
    - "**/Migrations/**"
    - "**/*.Designer.cs"
    - "**/*.generated.*"
    - "**/Program.cs"
    - "**/Startup.cs"
    - "**/*Dto.cs"
    - "**/*Dtos.cs"
    - "**/*.proto"
    - "*.sql"
  reason_blacklist:
    - 临时
    - 先这样
    - 历史原因
    - TODO
    - 待确认
    - quick fix
    - temp
    - wip
    - hack
    - for now

deliverhq_integration:
  enabled: ${DELIVERHQ_INTEGRATION}
  records_dirs:
    - "docs/requirements/"
    - "DeliverHQ/change-requests/"
  evidence_summary: "DeliverHQ/evidence-summary.json"
  # create_mr.py 据此从需求文档自动读"背景", AI 无需手传 --why
  requirement_doc_patterns:
    - "requirement.md"
    - "spec.md"
    - "README.md"
    - "index.md"
  background_headings:
    - "背景"
    - "Background"
    - "需求描述"
    - "目标"
EOF
  ok "写入 governance.config.yml"
fi

# ---------- 4. 扫描脚本 ----------
log "安装扫描脚本 -> governance/scripts/"
fetch_or_local "scripts/governance_common.py" | write_file "governance/scripts/governance_common.py"
fetch_or_local "scripts/scan_risks.py"      | write_file "governance/scripts/scan_risks.py"
fetch_or_local "scripts/validate_mr.py"     | write_file "governance/scripts/validate_mr.py"
fetch_or_local "scripts/report_expired.py"  | write_file "governance/scripts/report_expired.py"
fetch_or_local "scripts/collect_ai_usage.py" | write_file "governance/scripts/collect_ai_usage.py"
fetch_or_local "scripts/record_test_run.py" | write_file "governance/scripts/record_test_run.py"
fetch_or_local "scripts/check_tested.py"    | write_file "governance/scripts/check_tested.py"
fetch_or_local "scripts/create_mr.py"       | write_file "governance/scripts/create_mr.py"
fetch_or_local "scripts/install-hooks.sh"   | write_file "governance/scripts/install-hooks.sh"
fetch_or_local "scripts/selftest.sh"        | write_file "governance/scripts/selftest.sh"
chmod +x "${TARGET_DIR}/governance/scripts/selftest.sh" \
         "${TARGET_DIR}/governance/scripts/install-hooks.sh" 2>/dev/null || true

# 自动安装 AI-Usage 采集 git hook (提交时自动写 trailer, 无需人工填)
if [[ -d "${TARGET_DIR}/.git" ]]; then
  log "安装 AI-Usage 自动采集 git hook"
  ( cd "$TARGET_DIR" && bash governance/scripts/install-hooks.sh ) || \
    warn "git hook 安装失败, 可稍后手动运行 bash governance/scripts/install-hooks.sh"
else
  warn "非 git 仓库, 跳过 hook 安装 (稍后在仓库根运行 bash governance/scripts/install-hooks.sh)"
fi

# ---------- 5. CI 钩子片段 ----------
CI_SNIPPET="${TARGET_DIR}/governance/ci-snippet.yml"
mkdir -p "$(dirname "$CI_SNIPPET")"
cat > "$CI_SNIPPET" <<'EOF'
# 把下面的 include 加入 .gitlab-ci.yml:
#
# include:
#   - local: '/governance/ci-snippet.yml'
#
# governance job 在 test 阶段之前运行, diff-only, 秒级返回。

stages:
  - governance
  - test
  - build

# 风险扫描: 硬阻断 (命中风险模式且缺合法注解 → 拒合)
governance:risk-scan:
  stage: governance
  image: python:3.11-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH
  before_script:
    - pip install -q pyyaml==6.0.3
  script:
    - |
      # 用目标分支最新 tip 作 base (而非 MR 创建时的旧快照), 否则 merge 进来的
      # 他人改动会被误算进本次 diff。三点 diff 自动取 merge-base。
      TB="${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-$CI_DEFAULT_BRANCH}"
      git fetch -q origin "$TB"
      python governance/scripts/scan_risks.py --diff-base "origin/$TB"
  allow_failure: false

# 密钥扫描: 硬阻断 (本次 MR 引入的提交里出现密钥/凭据 → 拒合)
# 用官方 gitleaks 镜像, 只扫本次 MR 范围 (base..HEAD), 不扫历史全量。
governance:secret-scan:
  stage: governance
  image:
    name: zricethezav/gitleaks:v8.30.1
    entrypoint: [""]
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH
  variables:
    GIT_DEPTH: 0          # 需要完整历史以便按范围扫描
  script:
    - |
      TB="${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-$CI_DEFAULT_BRANCH}"
      git fetch -q origin "$TB"
      # 只扫本次 MR 引入的提交; 命中任一密钥 gitleaks 返回非 0
      gitleaks detect \
        --source . \
        --log-opts="origin/${TB}..HEAD" \
        --redact \
        --no-banner \
        --verbose
  allow_failure: false

# MR 描述校验: v1 软模式 (缺字段只警告, soft_deadline 后由脚本读 config 自动转硬)
governance:mr-validate:
  stage: governance
  image: python:3.11-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  variables:
    GIT_DEPTH: 0          # 需完整历史以读取 commit 里的 AI-Usage trailer
  before_script:
    - pip install -q pyyaml==6.0.3
  script:
    - |
      TB="${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-$CI_DEFAULT_BRANCH}"
      git fetch -q origin "$TB"
      echo "$CI_MERGE_REQUEST_DESCRIPTION" \
        | python governance/scripts/validate_mr.py --diff-base "origin/$TB"
  # 软模式期内脚本自身返回 0; deadline 过后脚本返回 1, 此时该 job 才真正阻断。
  # allow_failure 设 false 以便 deadline 后生效; 软模式期脚本不会 fail。
  allow_failure: false

# 测试痕迹检测: 改动的生产代码是否做过测试 (v1 软; 但失败测试记录无条件硬拦)
# CI 看不到 .governance/ 证据 (gitignore), 退回读 commit 的 Tested: trailer。
governance:test-check:
  stage: governance
  image: python:3.11-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH
  variables:
    GIT_DEPTH: 0          # 需完整历史以读取 commit 里的 Tested: trailer
  before_script:
    - pip install -q pyyaml==6.0.3
  script:
    - |
      TB="${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-$CI_DEFAULT_BRANCH}"
      git fetch -q origin "$TB"
      python governance/scripts/check_tested.py --diff-base "origin/$TB"
  # 软模式期脚本对"未测"仅警告返回 0; 但"失败测试记录"始终返回 1 硬拦。
  allow_failure: false

# 过期注解周报: 仅报告, 不阻断 (定时或手动触发)
# 在 GitLab → CI/CD → Schedules 里新建每周一 08:00 的定时 pipeline 即可自动运行。
governance:expired-report:
  stage: governance
  image: python:3.11-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "schedule"
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      when: manual
      allow_failure: true
    - if: $CI_COMMIT_BRANCH
      when: manual
      allow_failure: true
  before_script:
    - pip install -q pyyaml==6.0.3
  script:
    - |
      python governance/scripts/report_expired.py \
        --root . \
        --output governance/reports/expired-annotations.md
  artifacts:
    paths:
      - governance/reports/
    expire_in: 30 days
  allow_failure: true
EOF
ok "生成 CI 片段 governance/ci-snippet.yml (已接入真实扫描脚本)"

# ---------- 5. 完成提示 ----------
cat <<EOF

============================================================
 MR 治理规范 ${VERSION} 安装完成
============================================================

已安装文件:
  .gitlab/merge_request_templates/default.md
  docs/governance/mr-spec.md
  docs/governance/risk-types.md
  governance.config.yml         (soft_deadline = ${SOFT_DEADLINE})
  governance/ci-snippet.yml
  governance/scripts/scan_risks.py       (风险扫描, 硬门禁)
  governance/scripts/validate_mr.py     (MR 校验, 软门禁)
  governance/scripts/report_expired.py  (过期注解周报)
  governance/scripts/collect_ai_usage.py (AI 使用自动采集 -> commit trailer)
  governance/scripts/record_test_run.py (测试运行记录器 -> 留痕)
  governance/scripts/check_tested.py    (测试痕迹检测, 软门禁)
  governance/scripts/create_mr.py       (自动生成并提交 MR)
  governance/scripts/install-hooks.sh   (安装 prepare-commit-msg hook)
  governance/scripts/selftest.sh        (脚本自测)
  CLAUDE.md                     (Claude Code / Kiro)
  .hermes.md                    (Hermes Agent v0.17.0)
  AGENTS.md                     (OpenAI Codex CLI + Hermes fallback)
  .github/copilot-instructions.md
  .cursor/rules/governance.mdc

下一步:
  1. 在 GitLab Web UI 创建 MR 时, 选择模板 "default" 即可。
  2. 把 governance/ci-snippet.yml include 进 .gitlab-ci.yml:
       include:
         - local: '/governance/ci-snippet.yml'
  3. 验证脚本可用 (需 python3 + pyyaml + git):
       bash governance/scripts/selftest.sh
  4. 阅读规范: docs/governance/mr-spec.md
  5. v1 软模式将在 ${SOFT_DEADLINE} 到期, 届时未填字段会阻断合并。

AI-Usage 自动采集:
  - prepare-commit-msg hook 已安装, 提交时自动写 AI-Usage trailer, 无需人工填。
  - AI agent 开发时会把证据写入 .governance/ai-evidence.jsonl (已 gitignore)。
  - 手动预览本次将写入的 trailer:
       python governance/scripts/collect_ai_usage.py --staged

DeliverHQ 集成: $( [[ "$DELIVERHQ_INTEGRATION" == "true" ]] && echo "已启用" || echo "未启用 (无 DeliverHQ/ 目录)" )

如需提交本次安装, 建议:
  git checkout -b chore/governance-v1
  git add .gitlab/ docs/governance/ governance.config.yml governance/ \\
          CLAUDE.md .hermes.md AGENTS.md \\
          .github/copilot-instructions.md .cursor/rules/
  git commit -m "chore: install MR governance ${VERSION}"

EOF
