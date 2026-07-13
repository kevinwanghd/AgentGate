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

HOOK_DIR="${REPO_ROOT}/.git/hooks"
HOOK_FILE="${HOOK_DIR}/prepare-commit-msg"
PREVIOUS_HOOK="${HOOK_FILE}.agentgate-previous"
mkdir -p "$HOOK_DIR"

# 若已有同名 hook 且非本工具生成, 备份
if [[ -f "$HOOK_FILE" ]] && ! grep -q "governance:ai-usage" "$HOOK_FILE" 2>/dev/null; then
  cp -a "$HOOK_FILE" "$PREVIOUS_HOOK"
  echo "[hooks] 已保留原有 prepare-commit-msg, 安装后将继续调用"
fi

cat > "$HOOK_FILE" <<'HOOK'
#!/usr/bin/env bash
# governance:ai-usage — 自动把 AI-Usage trailer 写入 commit message
# 由 governance/scripts/install-hooks.sh 生成, 勿手改。
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
AI_SCRIPT="${REPO_ROOT}/governance/scripts/collect_ai_usage.py"
TEST_SCRIPT="${REPO_ROOT}/governance/scripts/check_tested.py"

PY="$(command -v python3 || command -v python || true)"
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
