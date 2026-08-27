#!/usr/bin/env bash
#
# install-hooks.sh — 安装 AI-Usage 自动采集 git hook
#
# 把 prepare-commit-msg hook 装进当前仓库的 .git/hooks/, 使每次 commit
# 自动调用 collect_ai_usage.py, 把 AI-Usage trailer 追加到 commit message。
# 全程无需人工填写。
#
# 用法:  bash governance/scripts/install-hooks.sh
#
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" ]]; then
  echo "[hooks] 错误: 当前目录不是 git 仓库。" >&2
  exit 1
fi

HOOK_DIR="$(git rev-parse --git-path hooks)"
HOOK_FILE="${HOOK_DIR}/prepare-commit-msg"
PRE_COMMIT_FILE="${HOOK_DIR}/pre-commit"
PREVIOUS_HOOK="${HOOK_FILE}.agentgate-previous"
mkdir -p "$HOOK_DIR"

# 若已有同名 hook 且非本工具生成, 备份
if [[ -f "$HOOK_FILE" ]] && ! grep -q "governance:ai-usage" "$HOOK_FILE" 2>/dev/null; then
  cp -a "$HOOK_FILE" "$PREVIOUS_HOOK"
  echo "[hooks] 已保留原有 prepare-commit-msg, 安装后将继续调用"
fi

cat > "$HOOK_FILE" <<'HOOK'
#!/bin/sh
# governance:ai-usage — 自动把 AI-Usage trailer 写入 commit message
# 由 governance/scripts/install-hooks.sh 生成, 勿手改。
AGENTGATE_ORIGINAL_PATH="${PATH:-}"
PATH="/usr/local/bin:/usr/bin:/bin:${AGENTGATE_ORIGINAL_PATH}"
export PATH

COMMIT_MSG_FILE="$1"
COMMIT_SOURCE="${2:-}"

LEGACY_HOOK="${0}.agentgate-previous"
if [ -x "$LEGACY_HOOK" ]; then
  "$LEGACY_HOOK" "$@" || exit $?
fi

# merge / squash / 已有 message 模板时跳过, 避免重复注入
case "$COMMIT_SOURCE" in
  merge|squash) exit 0 ;;
esac

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
# 兼容两种仓库形态: 下游仓库为 governance/scripts/, AgentGate 源仓库自身为 scripts/
AI_SCRIPT="${REPO_ROOT}/governance/scripts/collect_ai_usage.py"
[ -f "$AI_SCRIPT" ] || AI_SCRIPT="${REPO_ROOT}/scripts/collect_ai_usage.py"
TEST_SCRIPT="${REPO_ROOT}/governance/scripts/check_tested.py"
[ -f "$TEST_SCRIPT" ] || TEST_SCRIPT="${REPO_ROOT}/scripts/check_tested.py"

PY=""
for CANDIDATE in python python3; do
  CANDIDATE_PATH="$(PATH="$AGENTGATE_ORIGINAL_PATH" command -v "$CANDIDATE" 2>/dev/null || true)"
  if [ -n "$CANDIDATE_PATH" ] && "$CANDIDATE_PATH" -c 'raise SystemExit(0)' >/dev/null 2>&1; then
    PY="$CANDIDATE_PATH"
    break
  fi
done
[ -n "$PY" ] || exit 0

# 1. AI-Usage trailer (若尚未存在)
# 优先从 commit 自身采集（prepare-commit-msg 阶段已暂存，可直接分析）
# 降级场景：若无已安装的 hook，从 pending_trailer 读取 pre-commit 阶段采集的结果
if ! grep -qi '^AI-Usage:' "$COMMIT_MSG_FILE"; then
  T="$("$PY" "$AI_SCRIPT" --staged --trailer-only 2>/dev/null || true)"
  [ -z "$T" ] && [ -f "${REPO_ROOT}/.governance/pending_trailer" ] && \
    T="$(cat "${REPO_ROOT}/.governance/pending_trailer" 2>/dev/null || true)"
  [ -n "$T" ] && printf '\n%s\n' "$T" >> "$COMMIT_MSG_FILE"
fi

# 2. Tested trailer (若尚未存在) —— 供 CI 在证据文件 (gitignore) 不可见时读取
# 仅当有实质结果(pass/fail)才写; 证据缺失得到 "none" 时不写,
# 避免 rebase/squash 重写历史时用 Tested:none 覆盖掉原有的 Tested:pass。
if [ -f "$TEST_SCRIPT" ] && ! grep -qi '^Tested:' "$COMMIT_MSG_FILE"; then
  T="$("$PY" "$TEST_SCRIPT" --emit-trailer 2>/dev/null || true)"
  case "$T" in
    *pass*|*fail*) printf '%s\n' "$T" >> "$COMMIT_MSG_FILE" ;;
    *) : ;;  # none / 空 → 不写, 保留历史 trailer
  esac
fi
HOOK

chmod +x "$HOOK_FILE"

# ============================================================
# 安装降级 pre-commit hook (当 prepare-commit-msg 缺失时兜底)
# pre-commit 在 commit message 编辑前运行，可写入 AI-Usage trailer
# 但此时 commit message 尚未创建，所以我们改为在 pre-commit 里
# 把 trailer 写入 .governance/pending_trailer 文件，由下一个 prepare-commit-msg 读取
# ============================================================
cat > "$PRE_COMMIT_FILE" <<'PRECOMMIT'
#!/bin/sh
# governance:ai-usage-fallback — prepare-commit-msg 缺失时的降级方案
# 将 AI-Usage trailer 写入 .governance/pending_trailer，下一个 prepare-commit-msg 读取追加
AGENTGATE_ORIGINAL_PATH="${PATH:-}"
PATH="/usr/local/bin:/usr/bin:/bin:${AGENTGATE_ORIGINAL_PATH}"
export PATH

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
AI_SCRIPT="${REPO_ROOT}/governance/scripts/collect_ai_usage.py"
[ -f "$AI_SCRIPT" ] || AI_SCRIPT="${REPO_ROOT}/scripts/collect_ai_usage.py"

PY=""
for CANDIDATE in python python3; do
  CANDIDATE_PATH="$(PATH="$AGENTGATE_ORIGINAL_PATH" command -v "$CANDIDATE" 2>/dev/null || true)"
  if [ -n "$CANDIDATE_PATH" ] && "$CANDIDATE_PATH" -c 'raise SystemExit(0)' >/dev/null 2>&1; then
    PY="$CANDIDATE_PATH"
    break
  fi
done
[ -n "$PY" ] || exit 0

# 如果 prepare-commit-msg hook 存在且已处理 trailer，不需要这里重复写
PREPARE_HOOK="${REPO_ROOT}/.git/hooks/prepare-commit-msg"
if [ -f "$PREPARE_HOOK" ] && grep -q "governance:ai-usage" "$PREPARE_HOOK" 2>/dev/null; then
  # prepare-commit-msg 已安装，降级 hook 只做保险检查，不写 trailer
  # 检查 pending_trailer 是否残留，有则清理
  PENDING="${REPO_ROOT}/.governance/pending_trailer"
  [ -f "$PENDING" ] && rm -f "$PENDING"
  exit 0
fi

# 降级场景：prepare-commit-msg 缺失，pre-commit 是唯一机会
# 用 --staged 采集本次暂存的 diff 信息（因为 commit 尚未完成）
EVIDENCE="${REPO_ROOT}/.governance/ai-evidence.jsonl"
PENDING="${REPO_ROOT}/.governance/pending_trailer"
mkdir -p "${REPO_ROOT}/.governance"

# 如果已有 pending_trailer 且已写入本次 commit message，跳过
if [ -f "$PENDING" ]; then
  exit 0
fi

# 用 diff --cached 采集已暂存改动的 AI 使用信息
# 降级模式下无法知道 commit 后 trailer 位置，只能写临时文件
T="$("$PY" "$AI_SCRIPT" --staged --trailer-only 2>/dev/null || true)"
if [ -n "$T" ]; then
  # 只写一次，下次 pre-commit 检查是否存在，存在则跳过
  if [ ! -f "$PENDING" ]; then
    printf '%s\n' "$T" > "$PENDING"
  fi
fi
PRECOMMIT

chmod +x "$PRE_COMMIT_FILE"

# ============================================================
# 安装 post-commit hook：
# 1. 清理 pending_trailer
# 2. P1-1: 自动采集本次 commit 的 AI evidence（从 HEAD~1 比对）
# ============================================================
POST_COMMIT_FILE="${HOOK_DIR}/post-commit"
cat > "$POST_COMMIT_FILE" <<'POSTCOMMIT'
#!/bin/sh
# governance:post-commit — commit 成功后执行治理任务
# 1. 清理 pending_trailer
# 2. P1-1: 自动采集 AI evidence 到 .governance/ai-evidence.jsonl
AGENTGATE_ORIGINAL_PATH="${PATH:-}"
PATH="/usr/local/bin:/usr/bin:/bin:${AGENTGATE_ORIGINAL_PATH}"
export PATH

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo .)"

# 1. 清理 pending_trailer
PENDING="${REPO_ROOT}/.governance/pending_trailer"
[ -f "$PENDING" ] && rm -f "$PENDING"

# 2. P1-1: 自动采集本次 commit 的 AI evidence
AI_SCRIPT="${REPO_ROOT}/governance/scripts/collect_ai_usage.py"
[ -f "$AI_SCRIPT" ] || AI_SCRIPT="${REPO_ROOT}/scripts/collect_ai_usage.py"

PY=""
for CANDIDATE in python python3; do
  CANDIDATE_PATH="$(PATH="$AGENTGATE_ORIGINAL_PATH" command -v "$CANDIDATE" 2>/dev/null || true)"
  if [ -n "$CANDIDATE_PATH" ] && "$CANDIDATE_PATH" -c 'raise SystemExit(0)' >/dev/null 2>&1; then
    PY="$CANDIDATE_PATH"
    break
  fi
done

if [ -n "$PY" ] && [ -f "$AI_SCRIPT" ]; then
  # P1-1: commit 成功后，用 HEAD~1 比对自动采集本次改动的 AI 行数
  # 这不依赖 AI agent 手动记录，而是 hook 自动采集
  "$PY" "$AI_SCRIPT" --collect-commit --evidence "${REPO_ROOT}/.governance/ai-evidence.jsonl" 2>/dev/null || true
fi
POSTCOMMIT

chmod +x "$POST_COMMIT_FILE"

# 确保证据文件被 gitignore (会话产物, 不入库)
GITIGNORE="${REPO_ROOT}/.gitignore"
if ! grep -q "^\.governance/" "$GITIGNORE" 2>/dev/null; then
  {
    echo ""
    echo "# governance: AI 使用 / 测试运行证据 (会话产物, 不入库)"
    echo ".governance/"
  } >> "$GITIGNORE"
  echo "[hooks] 已把 .governance/ 加入 .gitignore"
fi

echo "[hooks] prepare-commit-msg 已安装到 ${HOOK_FILE}"
echo "[hooks] 之后每次 commit 会自动追加 AI-Usage 与 Tested trailer。"
