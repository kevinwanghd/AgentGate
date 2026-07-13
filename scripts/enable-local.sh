#!/usr/bin/env bash
#
# enable-local.sh — 在本地仓库一键启用 AgentGate 门禁
#
# 适用于"中心化形态"接入的仓库 (仓库内无脚本, 脚本在 AgentGate)。
# 它做三件事:
#   1. 把 AgentGate 脚本拉到本地缓存 (~/.agentgate, 已存在则更新)
#   2. 安装 prepare-commit-msg git hook (提交时自动盖 AI-Usage / Tested trailer)
#   3. 打印本地自查命令, 方便提交前手动跑
#
# 用法 (在你的目标仓库根目录执行):
#   bash enable-local.sh
#   或指定 AgentGate 来源 / 缓存位置:
#   AGENTGATE_REPO=https://github.com/youorg/AgentGate.git AGENTGATE_HOME=~/.agentgate bash enable-local.sh
#
set -euo pipefail

AGENTGATE_REPO="${AGENTGATE_REPO:-https://github.com/kevinwanghd/AgentGate.git}"
AGENTGATE_REF="${AGENTGATE_REF:-main}"
AGENTGATE_HOME="${AGENTGATE_HOME:-$HOME/.agentgate}"

log()  { printf '\033[1;34m[enable-local]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[err]\033[0m %s\n' "$*" >&2; }

# --- 0. 必须在 git 仓库根 ---
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" ]]; then
  err "当前目录不是 git 仓库。请在目标仓库根目录运行。"
  exit 1
fi
cd "$REPO_ROOT"

if [[ ! -f "governance.config.yml" ]]; then
  warn "未找到 governance.config.yml — 这个仓库可能还没接入 AgentGate。"
  warn "本脚本仍会装脚本和 hook, 但门禁配置缺失时脚本会用内置默认值。"
fi

# --- 1. 拉取 / 更新 AgentGate 脚本缓存 ---
if [[ -d "$AGENTGATE_HOME/.git" ]]; then
  log "更新 AgentGate 缓存: $AGENTGATE_HOME"
  git -C "$AGENTGATE_HOME" fetch -q origin "$AGENTGATE_REF"
  git -C "$AGENTGATE_HOME" checkout -q "$AGENTGATE_REF"
  git -C "$AGENTGATE_HOME" pull -q origin "$AGENTGATE_REF"
else
  log "拉取 AgentGate 到本地缓存: $AGENTGATE_HOME"
  git clone -q --depth 1 -b "$AGENTGATE_REF" "$AGENTGATE_REPO" "$AGENTGATE_HOME"
fi
ok "AgentGate 脚本就绪 ($AGENTGATE_HOME/scripts)"

# --- 2. 安装提交 hook (复用 AgentGate 的 install-hooks.sh) ---
# install-hooks.sh 默认假设脚本在 <repo>/governance/scripts; 中心化形态下脚本
# 不在本仓库, 所以这里直接装一个指向缓存目录的 hook。
HOOK_DIR="${REPO_ROOT}/.git/hooks"
HOOK_FILE="${HOOK_DIR}/prepare-commit-msg"
PREVIOUS_HOOK="${HOOK_FILE}.agentgate-previous"
mkdir -p "$HOOK_DIR"

if [[ -f "$HOOK_FILE" ]] && ! grep -q "agentgate:local" "$HOOK_FILE" 2>/dev/null; then
  cp -a "$HOOK_FILE" "$PREVIOUS_HOOK"
  warn "已保留原有 prepare-commit-msg, 安装后将继续调用"
fi

cat > "$HOOK_FILE" <<HOOK
#!/usr/bin/env bash
# agentgate:local — 提交时自动写 AI-Usage / Tested trailer (中心化缓存脚本)
COMMIT_MSG_FILE="\$1"; COMMIT_SOURCE="\${2:-}"
LEGACY_HOOK="\${0}.agentgate-previous"
if [ -x "\$LEGACY_HOOK" ]; then
  "\$LEGACY_HOOK" "\$@" || exit \$?
fi
case "\$COMMIT_SOURCE" in merge|squash) exit 0 ;; esac
AG="${AGENTGATE_HOME}/scripts"
PY="\$(command -v python3 || command -v python || true)"
[ -n "\$PY" ] || exit 0
if [ -f "\$AG/collect_ai_usage.py" ] && ! grep -qi '^AI-Usage:' "\$COMMIT_MSG_FILE"; then
  T="\$("\$PY" "\$AG/collect_ai_usage.py" --staged --trailer-only 2>/dev/null || true)"
  [ -n "\$T" ] && printf '\n%s\n' "\$T" >> "\$COMMIT_MSG_FILE"
fi
if [ -f "\$AG/check_tested.py" ] && ! grep -qi '^Tested:' "\$COMMIT_MSG_FILE"; then
  T="\$("\$PY" "\$AG/check_tested.py" --emit-trailer 2>/dev/null || true)"
  [ -n "\$T" ] && printf '%s\n' "\$T" >> "\$COMMIT_MSG_FILE"
fi
HOOK
chmod +x "$HOOK_FILE"
ok "已安装提交 hook: $HOOK_FILE"

# --- 3. 把证据文件加入 .gitignore ---
GI="${REPO_ROOT}/.gitignore"
if ! grep -q "^\.governance/" "$GI" 2>/dev/null; then
  { echo ""; echo "# AgentGate: 会话产物, 不入库"; echo ".governance/"; } >> "$GI"
  ok "已把 .governance/ 加入 .gitignore"
fi

# --- 完成提示 ---
cat <<EOF

============================================================
 本地门禁已启用
============================================================
之后每次 git commit 会自动追加 AI-Usage / Tested trailer。

提交前可手动自查 (可选):
  python ${AGENTGATE_HOME}/scripts/scan_risks.py --staged --config governance.config.yml
  python ${AGENTGATE_HOME}/scripts/check_tested.py --staged --config governance.config.yml

跑测试并留痕 (改了生产代码时):
  python ${AGENTGATE_HOME}/scripts/record_test_run.py -- <你的测试命令>

升级 AgentGate (拿最新脚本): 再跑一次本脚本即可。
EOF
