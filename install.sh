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
PLATFORM="gitlab"
MODE="pinned"
PROFILE="core"
AGENTGATE_REPO="kevinwanghd/AgentGate"
AGENTGATE_REF="github-stable"
CI_IMAGE_POLICY="${GOVERNANCE_CI_IMAGE_POLICY:-internal-only}"
CI_PY_IMAGE="${GOVERNANCE_PY_IMAGE:-swr.cn-north-4.myhuaweicloud.com/adbidding/governance/python-ci:latest}"
CI_GO_IMAGE="${GOVERNANCE_GO_IMAGE:-$CI_PY_IMAGE}"
CI_FLUTTER_IMAGE="${GOVERNANCE_FLUTTER_IMAGE:-$CI_PY_IMAGE}"
CI_PYTHON_TEST_IMAGE="${GOVERNANCE_PYTHON_TEST_IMAGE:-$CI_PY_IMAGE}"
CI_NODE_IMAGE="${GOVERNANCE_NODE_IMAGE:-$CI_PY_IMAGE}"
CI_JAVA_IMAGE="${GOVERNANCE_JAVA_IMAGE:-$CI_PY_IMAGE}"
CI_DOTNET_IMAGE="${GOVERNANCE_DOTNET_IMAGE:-$CI_PY_IMAGE}"
CI_RUST_IMAGE="${GOVERNANCE_RUST_IMAGE:-$CI_PY_IMAGE}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agents) AGENTS="$2"; shift 2 ;;
    --agents=*) AGENTS="${1#*=}"; shift ;;
    --platform) PLATFORM="$2"; shift 2 ;;
    --platform=*) PLATFORM="${1#*=}"; shift ;;
    --mode) MODE="$2"; shift 2 ;;
    --mode=*) MODE="${1#*=}"; shift ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --profile=*) PROFILE="${1#*=}"; shift ;;
    --agentgate-repo) AGENTGATE_REPO="$2"; shift 2 ;;
    --agentgate-repo=*) AGENTGATE_REPO="${1#*=}"; shift ;;
    --agentgate-ref) AGENTGATE_REF="$2"; shift 2 ;;
    --agentgate-ref=*) AGENTGATE_REF="${1#*=}"; shift ;;
    --ci-image-policy) CI_IMAGE_POLICY="$2"; shift 2 ;;
    --ci-image-policy=*) CI_IMAGE_POLICY="${1#*=}"; shift ;;
    --ci-all-image)
      CI_PY_IMAGE="$2"
      CI_GO_IMAGE="$2"
      CI_FLUTTER_IMAGE="$2"
      CI_PYTHON_TEST_IMAGE="$2"
      CI_NODE_IMAGE="$2"
      CI_JAVA_IMAGE="$2"
      CI_DOTNET_IMAGE="$2"
      CI_RUST_IMAGE="$2"
      shift 2
      ;;
    --ci-all-image=*)
      CI_PY_IMAGE="${1#*=}"
      CI_GO_IMAGE="${1#*=}"
      CI_FLUTTER_IMAGE="${1#*=}"
      CI_PYTHON_TEST_IMAGE="${1#*=}"
      CI_NODE_IMAGE="${1#*=}"
      CI_JAVA_IMAGE="${1#*=}"
      CI_DOTNET_IMAGE="${1#*=}"
      CI_RUST_IMAGE="${1#*=}"
      shift
      ;;
    --ci-python-image) CI_PY_IMAGE="$2"; shift 2 ;;
    --ci-python-image=*) CI_PY_IMAGE="${1#*=}"; shift ;;
    --ci-go-image) CI_GO_IMAGE="$2"; shift 2 ;;
    --ci-go-image=*) CI_GO_IMAGE="${1#*=}"; shift ;;
    --ci-flutter-image) CI_FLUTTER_IMAGE="$2"; shift 2 ;;
    --ci-flutter-image=*) CI_FLUTTER_IMAGE="${1#*=}"; shift ;;
    --ci-python-test-image) CI_PYTHON_TEST_IMAGE="$2"; shift 2 ;;
    --ci-python-test-image=*) CI_PYTHON_TEST_IMAGE="${1#*=}"; shift ;;
    --ci-node-image) CI_NODE_IMAGE="$2"; shift 2 ;;
    --ci-node-image=*) CI_NODE_IMAGE="${1#*=}"; shift ;;
    --ci-java-image) CI_JAVA_IMAGE="$2"; shift 2 ;;
    --ci-java-image=*) CI_JAVA_IMAGE="${1#*=}"; shift ;;
    --ci-dotnet-image) CI_DOTNET_IMAGE="$2"; shift 2 ;;
    --ci-dotnet-image=*) CI_DOTNET_IMAGE="${1#*=}"; shift ;;
    --ci-rust-image) CI_RUST_IMAGE="$2"; shift 2 ;;
    --ci-rust-image=*) CI_RUST_IMAGE="${1#*=}"; shift ;;
    *) TARGET_DIR="$1"; shift ;;
  esac
done
SOURCE_BASE="${GOVERNANCE_SOURCE:-}"
VERSION="v1.3.0"
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

append_governance_section() {
  local rel="$1"
  local source_rel="$2"
  local abs="${TARGET_DIR}/${rel}"
  mkdir -p "$(dirname "$abs")"
  if [[ -e "$abs" ]]; then
    if grep -q '<!-- governance-v1-begin -->' "$abs"; then
      ok "$rel already contains an AgentGate section; leaving the file unchanged"
      return 0
    else
      warn "$rel exists; appending governance section instead of overwriting repository instructions"
      {
        printf '\n\n---\n<!-- governance-v1-begin -->\n'
        fetch_or_local "$source_rel"
        printf '\n<!-- governance-v1-end -->\n'
      } >> "$abs"
      ok "appended governance section to $rel"
    fi
  else
    fetch_or_local "$source_rel" | write_file "$rel"
  fi
}

create_repository_lessons_file() {
  local rel="governance/lessons/repository.yml"
  local abs="${TARGET_DIR}/${rel}"
  mkdir -p "$(dirname "$abs")"
  if [[ -e "$abs" ]]; then
    ok "保留已有 $rel"
    return
  fi
  cat > "$abs" <<'EOF'
version: agentgate.io/lessons/v1
scope: repository
lessons: []
EOF
  ok "创建 $rel"
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

sed_replacement_escape() {
  printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}

is_public_image_ref() {
  local image="$1"
  case "$image" in
    python:*|node:*|golang:*|maven:*|rust:*|ubuntu:*|debian:*|alpine:*|docker:*) return 0 ;;
    mcr.microsoft.com/*|ghcr.io/*|docker.io/*|registry-1.docker.io/*|gcr.io/*|quay.io/*) return 0 ;;
  esac
  return 1
}

validate_ci_image_policy() {
  case "$CI_IMAGE_POLICY" in
    internal-only) ;;
    *) err "无效的 --ci-image-policy 值: $CI_IMAGE_POLICY (当前支持: internal-only)"; exit 1 ;;
  esac
  local name image
  for name in \
    CI_PY_IMAGE CI_GO_IMAGE CI_FLUTTER_IMAGE CI_PYTHON_TEST_IMAGE \
    CI_NODE_IMAGE CI_JAVA_IMAGE CI_DOTNET_IMAGE CI_RUST_IMAGE
  do
    image="${!name}"
    if [[ -z "$image" ]]; then
      err "$name 不能为空"
      exit 1
    fi
    if is_public_image_ref "$image"; then
      err "$name=$image 指向公网基础镜像；internal-only 策略下请改为内部预构建镜像或 CI 变量。"
      exit 1
    fi
  done
}

render_gitlab_ci_template() {
  fetch_or_local "ci/governance-ci.yml" | sed \
    -e "s|GOVERNANCE_PY_IMAGE: \".*\"|GOVERNANCE_PY_IMAGE: \"$(sed_replacement_escape "$CI_PY_IMAGE")\"|" \
    -e "s|GOVERNANCE_GO_IMAGE: \".*\"|GOVERNANCE_GO_IMAGE: \"$(sed_replacement_escape "$CI_GO_IMAGE")\"|" \
    -e "s|GOVERNANCE_FLUTTER_IMAGE: \".*\"|GOVERNANCE_FLUTTER_IMAGE: \"$(sed_replacement_escape "$CI_FLUTTER_IMAGE")\"|" \
    -e "s|GOVERNANCE_PYTHON_TEST_IMAGE: \".*\"|GOVERNANCE_PYTHON_TEST_IMAGE: \"$(sed_replacement_escape "$CI_PYTHON_TEST_IMAGE")\"|" \
    -e "s|GOVERNANCE_NODE_IMAGE: \".*\"|GOVERNANCE_NODE_IMAGE: \"$(sed_replacement_escape "$CI_NODE_IMAGE")\"|" \
    -e "s|GOVERNANCE_JAVA_IMAGE: \".*\"|GOVERNANCE_JAVA_IMAGE: \"$(sed_replacement_escape "$CI_JAVA_IMAGE")\"|" \
    -e "s|GOVERNANCE_DOTNET_IMAGE: \".*\"|GOVERNANCE_DOTNET_IMAGE: \"$(sed_replacement_escape "$CI_DOTNET_IMAGE")\"|" \
    -e "s|GOVERNANCE_RUST_IMAGE: \".*\"|GOVERNANCE_RUST_IMAGE: \"$(sed_replacement_escape "$CI_RUST_IMAGE")\"|"
}

# ---------- 前置检查 ----------
log "MR 治理规范 ${VERSION} 安装到: ${TARGET_DIR}"

if [[ ! -d "${TARGET_DIR}/.git" ]]; then
  warn "${TARGET_DIR} 不是 git 仓库根目录, 仍继续安装但 CI 集成可能失效。"
fi

validate_ci_image_policy

# ---------- GitLab URL / project id 自动探测 ----------
_detect_gitlab_url() {
  if [[ -n "${AGENTGATE_GITLAB_URL:-}" ]]; then echo "$AGENTGATE_GITLAB_URL"; return; fi
  if [[ -n "${CI_SERVER_URL:-}" ]]; then echo "$CI_SERVER_URL"; return; fi
  local remote_url
  remote_url="$(git -C "${TARGET_DIR}" remote get-url origin 2>/dev/null || true)"
  if [[ -z "$remote_url" ]]; then return; fi
  if [[ "$remote_url" =~ ^https?://[^@/]+@([^/]+)/ ]]; then echo "https://${BASH_REMATCH[1]}"
  elif [[ "$remote_url" =~ ^https?://([^/]+)/ ]]; then echo "https://${BASH_REMATCH[1]}"
  elif [[ "$remote_url" =~ ^[^@]+@([^:]+): ]]; then echo "https://${BASH_REMATCH[1]}"; fi
}

_detect_gitlab_project_id() {
  local gitlab_url="$1"
  if [[ -n "${CI_PROJECT_ID:-}" ]]; then echo "$CI_PROJECT_ID"; return; fi
  if [[ -n "${AGENTGATE_GITLAB_PROJECT_ID:-}" ]]; then echo "$AGENTGATE_GITLAB_PROJECT_ID"; return; fi
  local token="${AGENTGATE_GITLAB_TOKEN:-}"
  if [[ -z "$token" || -z "$gitlab_url" ]]; then return; fi
  local repo_name result remote_path
  repo_name="$(basename "${TARGET_DIR}")"
  result="$(curl -sf --max-time 5 -H "PRIVATE-TOKEN: $token" \
    "${gitlab_url}/api/v4/projects?search=${repo_name}&per_page=10" 2>/dev/null || true)"
  [[ -z "$result" ]] && return
  remote_path="$(git -C "${TARGET_DIR}" remote get-url origin 2>/dev/null | sed 's|.*://[^/]*/||;s|\.git$||' || true)"
  printf '%s' "$result" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); r=sys.argv[1].lower(); [print(p['id']) or exit() for p in d if p.get('path_with_namespace','').lower()==r]" \
    "$remote_path" 2>/dev/null || true
}

DETECTED_GITLAB_URL="$(_detect_gitlab_url || true)"
DETECTED_PROJECT_ID="$(_detect_gitlab_project_id "${DETECTED_GITLAB_URL}" || true)"

if [[ -n "$DETECTED_GITLAB_URL" && -n "$DETECTED_PROJECT_ID" ]]; then
  ok "自动探测到 GitLab: ${DETECTED_GITLAB_URL}，project id: ${DETECTED_PROJECT_ID}"
elif [[ -n "$DETECTED_GITLAB_URL" ]]; then
  warn "探测到 GitLab URL: ${DETECTED_GITLAB_URL}，但无法自动查 project id（缺少 AGENTGATE_GITLAB_TOKEN）"
  warn "安装完成后请在 governance.config.yml 中手动填写 create_mr.gitlab_project_id"
else
  warn "无法自动探测 GitLab URL，安装完成后请手动填写 governance.config.yml 的 create_mr 块"
fi

# ---------- 自动检测 SOURCE_DIR ----------
# 如果脚本是从仓库内 governance/ 目录运行, 直接复用本地文件
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/mr-spec.md" && -f "${SCRIPT_DIR}/risk-types.md" ]]; then
  SOURCE_DIR="$SCRIPT_DIR"
  log "检测到本地源: ${SOURCE_DIR}"
fi

case "$PLATFORM" in
  github|gitlab) ;;
  *)
    err "无效的 --platform 值: $PLATFORM (可选: github/gitlab)"
    exit 1
    ;;
esac

case "$PROFILE" in
  core|flutter-mobile|dotnet-monorepo|go-bazel) ;;
  *) err "无效的 --profile 值: $PROFILE (可选: core/flutter-mobile/dotnet-monorepo/go-bazel)"; exit 1 ;;
esac

case "$PROFILE" in
  core) PROFILE_REQUIRED_YAML='' ;;
  flutter-mobile) PROFILE_REQUIRED_YAML=$'      - flutter-analyze\n      - flutter-test' ;;
  dotnet-monorepo) PROFILE_REQUIRED_YAML='      - dotnet-test' ;;
  go-bazel) PROFILE_REQUIRED_YAML='      - bazel-test' ;;
esac

case "$MODE" in
  thin|pinned) ;;
  *)
    err "无效的 --mode 值: $MODE (可选: thin/pinned)"
    exit 1
    ;;
esac

if [[ "$PLATFORM" == "gitlab" && "$MODE" == "thin" ]]; then
  err "GitLab thin 模式需要 GitLab 中央 include 发布线, 请使用 --mode pinned 或手动 include gitlab/ci-snippet.yml@gitlab-stable"
  exit 1
fi

if [[ "$PLATFORM" == "github" && "$MODE" != "thin" ]]; then
  err "GitHub 目前只支持 --mode thin, 避免把中央脚本复制到业务仓库"
  exit 1
fi

# ---------- DeliverHQ 共存检测 ----------
DELIVERHQ_INTEGRATION="false"
if [[ -d "${TARGET_DIR}/DeliverHQ" ]]; then
  DELIVERHQ_INTEGRATION="true"
  ok "检测到 DeliverHQ/ 目录, 自动启用共存模式"
fi

# ---------- 1. MR / PR 模板 ----------
if [[ "$PLATFORM" == "gitlab" ]]; then
  log "安装 MR 模板 -> .gitlab/merge_request_templates/default.md"
  fetch_or_local "templates/merge_request_default.md" \
    | write_file ".gitlab/merge_request_templates/default.md"
else
  log "安装 PR 模板 -> .github/pull_request_template.md"
  fetch_or_local "templates/merge_request_default.md" \
    | write_file ".github/pull_request_template.md"
fi

# ---------- 2. 规范文档 ----------
log "安装规范文档 -> docs/governance/"
fetch_or_local "mr-spec.md"    | write_file "docs/governance/mr-spec.md"
fetch_or_local "risk-types.md" | write_file "docs/governance/risk-types.md"

# ---------- 3. AI Agent 指令文件 ----------
# --agents 选项: all(全部) / claude / copilot / cursor / hermes / codex / none
case "$AGENTS" in
  all|claude|copilot|cursor|hermes|codex)
    log "安装 AI agent 指令文件 (--agents=$AGENTS)"
    ;;
  none)
    log "跳过 AI agent 指令文件 (--agents=none)"
    ;;
  *)
    err "无效的 --agents 值: $AGENTS (可选: all/claude/copilot/cursor/hermes/codex/none)"
    exit 1
    ;;
esac

# Claude Code / Kiro
if [[ "$AGENTS" == "all" || "$AGENTS" == "claude" ]]; then
  append_governance_section "CLAUDE.md" "agent-instructions/CLAUDE.md"
fi

# GitHub Copilot
if [[ "$AGENTS" == "all" || "$AGENTS" == "copilot" ]]; then
  append_governance_section ".github/copilot-instructions.md" "agent-instructions/copilot-instructions.md"
fi

# Cursor
if [[ "$AGENTS" == "all" || "$AGENTS" == "cursor" ]]; then
  append_governance_section ".cursor/rules/governance.mdc" "agent-instructions/cursor-rules.mdc"
fi

# Hermes Agent
if [[ "$AGENTS" == "all" || "$AGENTS" == "hermes" ]]; then
  append_governance_section ".hermes.md" "agent-instructions/hermes-instructions.md"
fi

# OpenAI Codex / generic agent fallback
if [[ "$AGENTS" == "all" || "$AGENTS" == "codex" ]]; then
  append_governance_section "AGENTS.md" "agent-instructions/AGENTS.md"
fi

# ---------- 3. governance.config.yml ----------
log "生成 governance.config.yml"
CONFIG_PATH="${TARGET_DIR}/governance.config.yml"
if [[ -e "$CONFIG_PATH" ]]; then
  warn "governance.config.yml 已存在, 跳过生成 (如需重置, 先手动删除)"
  # 若已存在但缺少 create_mr 块，自动追加（不覆盖用户已有配置）
  if ! grep -q "^create_mr:" "$CONFIG_PATH"; then
    if [[ -n "$DETECTED_GITLAB_URL" && -n "$DETECTED_PROJECT_ID" ]]; then
      cat >> "$CONFIG_PATH" <<CEOF

create_mr:
  gitlab_url: "${DETECTED_GITLAB_URL}"
  gitlab_project_id: "${DETECTED_PROJECT_ID}"
  # AI agent 创建 MR 时直连 GitLab API，不依赖 glab/gh CLI
  # 个人 token 放本机环境变量 AGENTGATE_GITLAB_TOKEN（不要提交进仓库）
CEOF
      ok "已追加 create_mr 块到 governance.config.yml (url=${DETECTED_GITLAB_URL}, id=${DETECTED_PROJECT_ID})"
    else
      warn "无法自动追加 create_mr 块：缺少探测结果，请手动在 governance.config.yml 末尾添加 create_mr.gitlab_url 和 create_mr.gitlab_project_id"
    fi
  fi
else
  cat > "$CONFIG_PATH" <<EOF
# MR 治理规范配置 v1
version: ${VERSION}

metadata:
  enforcement: hard           # 缺字段直接阻断；治理检查默认禁止违规
  soft_deadline: ${SOFT_DEADLINE}   # 仅供显式 soft 过渡配置使用
  mandatory_fields:
    - background          # ## 背景
    - changes             # ## 变更内容
    - ai_usage            # AI-Usage 字段
    - self_test           # ## 自测确认

risk_annotations:
  enforcement: hard           # 风险注解不合规直接阻断
  reviewed_max_age_days: 90   # 3 个月 (推荐: 与季度 review 节奏对齐)
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

ci:
  image_policy: ${CI_IMAGE_POLICY}
  # AgentGate 生成的 GitLab job 不主动拉公网镜像；所有镜像必须来自内部预构建镜像或 CI 变量。
  images:
    python: "${CI_PY_IMAGE}"
    go: "${CI_GO_IMAGE}"
    flutter: "${CI_FLUTTER_IMAGE}"
    python_test: "${CI_PYTHON_TEST_IMAGE}"
    node: "${CI_NODE_IMAGE}"
    java: "${CI_JAVA_IMAGE}"
    dotnet: "${CI_DOTNET_IMAGE}"
    rust: "${CI_RUST_IMAGE}"

auto_merge:
  enabled: true
  strategy: squash
  delete_branch_after_merge: true
  require_up_to_date_branch: true
  require_all_required_checks: true
  required_checks_by_risk:
    low:
      - risk-scan
      - secret-scan
      - mr-validate
    medium:
      - risk-scan
      - secret-scan
      - mr-validate
      - test-check
${PROFILE_REQUIRED_YAML}
    high:
      - risk-scan
      - secret-scan
      - mr-validate
      - test-check
${PROFILE_REQUIRED_YAML}
    critical:
      - risk-scan
      - secret-scan
      - mr-validate
      - test-check
${PROFILE_REQUIRED_YAML}
  required_checks:
    - risk-scan
    - secret-scan
    - mr-validate
    - test-check
  protected_paths:
    - AGENTS.md
    - CLAUDE.md
    - .hermes.md
    - .github/copilot-instructions.md
    - .cursor/rules/**
    - governance.config.yml
    - .github/workflows/**
    - .gitlab-ci.yml
    - ci/**
    - governance/**
    - CODEOWNERS
    - scripts/scan_risks.py
    - scripts/check_tested.py
    - scripts/validate_mr.py
    - scripts/gate_decision.py
  risk_paths:
    low:
      - docs/**
      - "*.md"
    high:
      - "**/auth/**"
      - "**/payment/**"
      - "**/deploy/**"
      - .github/workflows/**
      - .gitlab-ci.yml
      - ci/**
    critical:
      - governance.config.yml
      - governance/**

testing:
  enforcement: hard           # 生产代码缺少测试证据直接阻断
  soft_deadline: ${SOFT_DEADLINE}   # 仅供显式 soft 过渡配置使用
  accept_tested_trailer: false # Tested: 仅作开发声明，合并放行只看 CI Evidence Bundle
  untested_max_age_days: 90   # risk:untested 注解有效期 (推荐: 与季度 review 节奏对齐)
  exclude_paths:              # 整目录/模式免测试检查 (DTO/迁移/生成代码/启动引导)
    - "governance/scripts/**"  # AgentGate vendored tooling is tested upstream
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

create_mr:
  gitlab_url: "${DETECTED_GITLAB_URL}"
  gitlab_project_id: "${DETECTED_PROJECT_ID}"
  # AI agent 创建 MR 时直连 GitLab API，不依赖 glab/gh CLI
  # 个人 token 放本机环境变量 AGENTGATE_GITLAB_TOKEN（不要提交进仓库）
EOF
  ok "写入 governance.config.yml"
fi

if [[ "$PLATFORM" == "github" && "$MODE" == "thin" ]]; then
  log "安装 GitHub 薄入口 -> .github/workflows/agentgate.yml"
  fetch_or_local "templates/github_agentgate_workflow.yml" \
    | sed \
        -e "s#__AGENTGATE_REPO__#${AGENTGATE_REPO}#g" \
        -e "s#__AGENTGATE_REF__#${AGENTGATE_REF}#g" \
    | write_file ".github/workflows/agentgate.yml"

  cat <<EOF

============================================================
 AgentGate GitHub thin onboarding complete
============================================================

Installed files:
  .github/workflows/agentgate.yml
  .github/pull_request_template.md
  docs/governance/mr-spec.md
  docs/governance/risk-types.md
  governance.config.yml

This repository now calls:
  ${AGENTGATE_REPO}/.github/workflows/agentgate.yml@${AGENTGATE_REF}

AgentGate scripts are not copied into this repository. Updating ${AGENTGATE_REF}
in the central AgentGate repository updates all GitHub repositories that call it.

GitLab repositories are not affected by this GitHub thin entrypoint.

EOF
  exit 0
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
fetch_or_local "scripts/gate_decision.py"   | write_file "governance/scripts/gate_decision.py"
fetch_or_local "scripts/validate_lessons.py" | write_file "governance/scripts/validate_lessons.py"
fetch_or_local "scripts/scan_secrets.py" | write_file "governance/scripts/scan_secrets.py"
fetch_or_local "scripts/gitlab_controller.py" | write_file "governance/scripts/gitlab_controller.py"
fetch_or_local "scripts/gitlab_mr_compat.py" | write_file "governance/scripts/gitlab_mr_compat.py"
fetch_or_local "scripts/evidence_bundle.py" | write_file "governance/scripts/evidence_bundle.py"
fetch_or_local "scripts/risk_merge_decision.py" | write_file "governance/scripts/risk_merge_decision.py"
fetch_or_local "scripts/create_mr.py"       | write_file "governance/scripts/create_mr.py"
fetch_or_local "scripts/agentgate.py"       | write_file "governance/scripts/agentgate.py"
fetch_or_local "scripts/run_affected_tests.py" | write_file "governance/scripts/run_affected_tests.py"
fetch_or_local "scripts/install-hooks.sh"   | write_file "governance/scripts/install-hooks.sh"
fetch_or_local "scripts/selftest.sh"        | write_file "governance/scripts/selftest.sh"
chmod +x "${TARGET_DIR}/governance/scripts/selftest.sh" \
         "${TARGET_DIR}/governance/scripts/install-hooks.sh" 2>/dev/null || true

# ---------- 4b. 语言风险规则包 ----------
log "安装语言风险规则包 -> governance/patterns/"
fetch_or_local "patterns/go.yml" | write_file "governance/patterns/go.yml"
fetch_or_local "patterns/csharp.yml" | write_file "governance/patterns/csharp.yml"
fetch_or_local "patterns/python.yml" | write_file "governance/patterns/python.yml"
fetch_or_local "patterns/javascript.yml" | write_file "governance/patterns/javascript.yml"
fetch_or_local "patterns/java.yml" | write_file "governance/patterns/java.yml"
fetch_or_local "patterns/dart.yml" | write_file "governance/patterns/dart.yml"

# ---------- 4b2. 硬教训规则 ----------
log "安装硬教训规则 -> governance/lessons/"
fetch_or_local "lessons/gitlab-legacy-ci.yml" | write_file "governance/lessons/gitlab-legacy-ci.yml"
fetch_or_local "lessons/agent-instructions.yml" | write_file "governance/lessons/agent-instructions.yml"
fetch_or_local "lessons/agentgate-operations.yml" | write_file "governance/lessons/agentgate-operations.yml"
create_repository_lessons_file

# ---------- 4c. 语言验证 profile ----------
log "安装语言验证 profile -> governance/profiles/"
fetch_or_local "profiles/${PROFILE}.yml" | write_file "governance/profiles/${PROFILE}.yml"

# 自动安装 AI-Usage 采集 git hook (提交时自动写 trailer, 无需人工填)
if git -C "$TARGET_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "安装 AI-Usage 自动采集 git hook"
  ( cd "$TARGET_DIR" && bash governance/scripts/install-hooks.sh ) || \
    warn "git hook 安装失败, 可稍后手动运行 bash governance/scripts/install-hooks.sh"
else
  warn "非 git 仓库, 跳过 hook 安装 (稍后在仓库根运行 bash governance/scripts/install-hooks.sh)"
fi

# ---------- 5. CI 钩子片段 ----------
CI_SNIPPET="${TARGET_DIR}/governance/ci-snippet.yml"
mkdir -p "$(dirname "$CI_SNIPPET")"
render_gitlab_ci_template | write_file "governance/ci-snippet.yml"
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
  governance/scripts/gate_decision.py   (GateResult 决策 -> 自动合并/等待审批/阻断)
  governance/scripts/validate_lessons.py (hard lesson 可执行约束校验)
  governance/scripts/gitlab_controller.py (GitLab 11.4 Bot/API/P0 预检 + 自动 MR)
  governance/scripts/evidence_bundle.py (Evidence Plan/Bundle v2 生成与校验)
  governance/scripts/risk_merge_decision.py (风险分级/审批/自动合并决策 + 审计)
  governance/scripts/create_mr.py       (自动生成并提交 MR)
  governance/scripts/run_affected_tests.py (Go 受影响包测试 + 反向依赖)
  governance/scripts/install-hooks.sh   (安装 prepare-commit-msg hook)
  governance/scripts/selftest.sh        (脚本自测)
  governance/patterns/go.yml            (Go 专属风险规则包: warn 模式)
  governance/patterns/csharp.yml        (C# / .NET 专属风险规则包: warn 模式)
  governance/patterns/python.yml        (Python 专属风险规则包: warn 模式)
  governance/patterns/javascript.yml    (JavaScript/TypeScript 专属风险规则包: warn 模式)
  governance/patterns/java.yml          (Java 专属风险规则包: warn 模式)
  governance/patterns/dart.yml          (Dart/Flutter 专属风险规则包: warn 模式)
  governance/lessons/*.yml              (hard lesson 可执行约束)
  governance/lessons/repository.yml     (本仓库本地 lessons, 已存在则保留)
  governance/profiles/${PROFILE}.yml       (语言验证 profile)
  CI 镜像策略: ${CI_IMAGE_POLICY}
  CI Python 镜像: ${CI_PY_IMAGE}
  CI .NET 镜像: ${CI_DOTNET_IMAGE}
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
