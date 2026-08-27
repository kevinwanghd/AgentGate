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
if [ -f "$AI_SCRIPT" ] && ! grep -qi '^AI-Usage:' "$COMMIT_MSG_FILE"; then
  T="$("$PY" "$AI_SCRIPT" --staged --trailer-only 2>/dev/null || true)"
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
# pre-push fallback hook: prepare-commit-msg 被 --no-verify / merge / squash
# 跳过时, push 阶段补写 AI-Usage trailer（治理信号不丢失）
# ============================================================
PRE_PUSH_FILE="${HOOK_DIR}/pre-push"
cat > "$PRE_PUSH_FILE" <<'PREPUSH'
#!/bin/sh
# governance:pre-push fallback — 补写 prepare-commit-msg 跳过的 AI-Usage trailer
# 由 governance/scripts/install-hooks.sh 生成, 勿手改。
# 仅在 prepare-commit-msg 未写入 trailer 时触发。

AGENTGATE_ORIGINAL_PATH="${PATH:-}"
PATH="/usr/local/bin:/usr/bin:/bin:${AGENTGATE_ORIGINAL_PATH}"
export PATH

# 只处理本次 push 的 HEAD 提交
REMOTE="$1"
URL="$2"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo .)"

# 兼容两种仓库形态
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
[ -z "$PY" ] && exit 0

# 检查最近一个 commit 是否已有 AI-Usage trailer
# 用 --amend 时同一 commit 会被推送两次，但 amend 后已含 trailer，第二次 push 不重复写
if [ -f "$AI_SCRIPT" ]; then
  TRAILER="$("$PY" "$AI_SCRIPT" --pre-push 2>/dev/null || true)"
  if [ -n "$TRAILER" ]; then
    echo "[agentgate:pre-push] 补充写入 AI-Usage trailer (prepare-commit-msg 跳过的降级补偿)"
    # 仅提示，不强制 rewrite 历史（pre-push 无法安全 amend 已 push 的 commit）
    echo "[agentgate:pre-push] 建议: 运行 'git commit --amend' 加入 trailer，或在下一次 commit 使用 hook"
  fi
fi
PREPUSH

chmod +x "$PRE_PUSH_FILE"

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
echo "[hooks] pre-push fallback 已安装到 ${PRE_PUSH_FILE} (prepare-commit-msg 跳过时的降级补偿)"
