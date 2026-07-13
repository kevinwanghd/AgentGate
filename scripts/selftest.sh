#!/usr/bin/env bash
#
# selftest.sh — governance 扫描脚本自测
#
# 构造命中/合规样例, 验证 scan_risks.py 与 validate_mr.py 的拦截与放行行为。
# 不依赖外部网络, 只需 python3 + pyyaml + git。
#
# 用法:  bash governance/scripts/selftest.sh
# 退出码: 0 全部通过 / 1 有用例未达预期
#
set -uo pipefail

PYTHON_BIN="${AGENTGATE_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import sys' >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "[selftest] 找不到 python3 或 python" >&2
  exit 2
fi
python3() { "$PYTHON_BIN" "$@"; }
BASH_BIN="$(command -v bash)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCAN="${SCRIPT_DIR}/scan_risks.py"
VAL="${SCRIPT_DIR}/validate_mr.py"
TODAY="$(date +%Y-%m-%d)"
EMPTY_TREE="4b825dc642cb6eb9a060e54bf8d69288fbee4904"

PASS=0
FAIL=0

# 检测 pyyaml: 缺失时脚本退化为内置默认 config (soft/无 deadline),
# 依赖自定义 config 的硬模式用例无法生效, 这些用例会被跳过而非误判失败。
if python3 -c "import yaml" >/dev/null 2>&1; then
  HAS_YAML=1
else
  HAS_YAML=0
fi

skip() { echo "  ⊘ $1 (跳过: 环境无 pyyaml, 无法读自定义 config)"; }

# 临时工作区
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
git init -q
git config user.email t@t; git config user.name t

# 断言: 期望退出码
expect() {
  local name="$1" want="$2" got="$3"
  if [[ "$got" == "$want" ]]; then
    echo "  ✓ $name (exit=$got)"
    PASS=$((PASS+1))
  else
    echo "  ✗ $name (期望 exit=$want, 实际 $got)"
    FAIL=$((FAIL+1))
  fi
}

mkdiff() { # $1=file  → 生成相对空树的 diff
  git add "$1" >/dev/null 2>&1
  git commit -qm "add $1" >/dev/null 2>&1
  git diff --unified=0 --no-color "$EMPTY_TREE" HEAD -- "$1"
}

echo "== scan_risks.py =="

# 1. 命中无注解 → FAIL
cat > Bad.cs <<EOF
public class S {
    bool C(string adminUserId) {
        if (adminUserId == "626786582b50ab8ec08b0fa0") { return true; }
        return false;
    }
}
EOF
# 结构性违规测试用 hard 配置 (默认已改 soft, 需显式 hard 才拦实质问题)
cat > hard_scan.yml <<'CFG'
risk_annotations:
  enforcement: hard
  reviewed_max_age_days: 180
  registered_types: [auth-bypass, magic-id, swallowed-exception, suppressed-warning, skipped-test, time-bypass, env-hardcode, todo-no-context, test-removal]
  reason_blacklist: ["临时", "先这样", "历史原因", hack, wip]
CFG
mkdiff Bad.cs > d.diff
python3 "$SCAN" --diff-file d.diff --config hard_scan.yml >/dev/null 2>&1
expect "命中无注解应拦截(hard)" 1 $?

# 2. 命中有合法注解 → PASS
cat > Good.cs <<EOF
public class S {
    bool C(string adminUserId) {
        // risk:auth-bypass reason:"机器人账号用于数据同步无人工登录" owner:@team reviewed:$TODAY
        // risk:magic-id reason:"机器人账号标识由外部系统固定分配" owner:@team reviewed:$TODAY
        if (adminUserId == "626786582b50ab8ec08b0fa0") { return true; }
        return false;
    }
}
EOF
mkdiff Good.cs > d.diff
python3 "$SCAN" --diff-file d.diff >/dev/null 2>&1
expect "合法注解应放行" 0 $?

# 3. 黑名单词理由 → FAIL
cat > Black.cs <<EOF
public class S {
    bool C(string userId) {
        // risk:auth-bypass reason:"临时先这样处理一下登录" owner:@team reviewed:$TODAY
        if (userId == "626786582b50ab8ec08b0fa0") { return true; }
        return false;
    }
}
EOF
mkdiff Black.cs > d.diff
python3 "$SCAN" --diff-file d.diff --config hard_scan.yml >/dev/null 2>&1
expect "黑名单词理由应拦截(hard)" 1 $?

# 4. 过期注解 → FAIL
cat > Exp.cs <<EOF
public class S {
    bool C(string userId) {
        // risk:auth-bypass reason:"机器人账号同步数据无需人工登录" owner:@team reviewed:2020-01-01
        // risk:magic-id reason:"机器人账号标识由外部系统固定分配" owner:@team reviewed:2020-01-01
        if (userId == "626786582b50ab8ec08b0fa0") { return true; }
        return false;
    }
}
EOF
mkdiff Exp.cs > d.diff
python3 "$SCAN" --diff-file d.diff --config hard_scan.yml >/dev/null 2>&1
expect "过期注解降级为软提醒不阻断(新设计)" 0 $?

# 5. 干净代码 → PASS
cat > Clean.cs <<EOF
public class Calc {
    int Add(int a, int b) { return a + b; }
}
EOF
mkdiff Clean.cs > d.diff
python3 "$SCAN" --diff-file d.diff >/dev/null 2>&1
expect "干净代码不误报" 0 $?

# 6. 删除测试无注解 → FAIL
cat > del.diff <<'EOF'
diff --git a/T.cs b/T.cs
--- a/T.cs
+++ b/T.cs
@@ -1,4 +1,1 @@
-    [Fact]
-    public void Works() { Assert.True(true); }
EOF
python3 "$SCAN" --diff-file del.diff --config hard_scan.yml >/dev/null 2>&1
expect "删测试无注解应拦截(hard)" 1 $?

echo
echo "== validate_mr.py =="

cat > soft.yml <<'EOF'
metadata:
  enforcement: soft
  soft_deadline: 2099-12-31
  mandatory_fields: [background, changes, ai_usage, self_test]
EOF
cat > hard.yml <<'EOF'
metadata:
  enforcement: soft
  soft_deadline: 2020-01-01
  mandatory_fields: [background, changes, ai_usage, self_test]
EOF

cat > good_mr.md <<'EOF'
## 背景
机器人投放任务偶发重复, 需要加幂等。
## 变更内容
- 加分布式锁
- 执行前查重
## AI 使用声明
- AI-Usage: heavy
- AI-Tools: claude-code
## 自测确认
- [x] dotnet build
- [x] dotnet test
EOF

cat > bad_mr.md <<'EOF'
## 背景
改了点东西。
EOF

python3 "$VAL" --file good_mr.md --config soft.yml >/dev/null 2>&1
expect "合规描述软模式应通过" 0 $?

python3 "$VAL" --file bad_mr.md --config soft.yml >/dev/null 2>&1
expect "缺字段软模式仅警告不阻断" 0 $?

if [[ "$HAS_YAML" == "1" ]]; then
  python3 "$VAL" --file bad_mr.md --config hard.yml >/dev/null 2>&1
  expect "缺字段deadline已过应阻断" 1 $?

  # 空模板的占位符不算填写 → 硬模式 FAIL
  TPL="${SCRIPT_DIR}/../templates/merge_request_default.md"
  if [[ -f "$TPL" ]]; then
    python3 "$VAL" --file "$TPL" --config hard.yml >/dev/null 2>&1
    expect "空模板占位符不算填写" 1 $?
  fi
else
  skip "缺字段deadline已过应阻断"
  skip "空模板占位符不算填写"
fi

# == report_expired.py ==
echo
echo "== report_expired.py =="
REP="${SCRIPT_DIR}/report_expired.py"

# 隔离子目录, 避免 scan_risks.py 测试遗留的过期 .cs 文件干扰
mkdir -p rep_dir

# 过期注解
cat > rep_dir/expired_src.py <<EOF
# risk:auth-bypass reason:"机器人账号免登" owner:@ops reviewed:2024-01-01
if adminUserId == "626786582b50ab8ec08b0fa0":
    pass
EOF

# 有效注解 (今天)
cat > rep_dir/fresh_src.py <<EOF
# risk:magic-id reason:"机器人账号，永久有效" owner:@ops reviewed:${TODAY}
x = "626786582b50ab8ec08b0fa0"
EOF

# 有过期注解 → exit 0 (报告不阻断)
python3 "$REP" --root rep_dir --output /tmp/gov_rep.md >/dev/null 2>&1
expect "report_expired 有过期注解仍退出0" 0 $?
grep -q "已过期" /tmp/gov_rep.md
expect "report_expired 报告含过期条目" 0 $?

# --fail-on-expired 时过期注解应返回 1
python3 "$REP" --root rep_dir --fail-on-expired --output /dev/null >/dev/null 2>&1
expect "report_expired --fail-on-expired 返回1" 1 $?

# 移除过期文件 → --fail-on-expired 应返回 0
rm -f rep_dir/expired_src.py
python3 "$REP" --root rep_dir --fail-on-expired --output /dev/null >/dev/null 2>&1
expect "report_expired 全有效注解返回0" 0 $?

# == collect_ai_usage.py ==
echo
echo "== collect_ai_usage.py =="
COLLECT="${SCRIPT_DIR}/collect_ai_usage.py"

mkdir -p ai_dir/.governance ai_dir/src
( cd ai_dir && git init -q && git config user.email t@t && git config user.name t \
  && echo base > seed.txt && git add seed.txt && git commit -qm seed )

# 暂存一个 80 行源码文件
python3 -c "print(chr(10).join('line%d'%i for i in range(80)))" > ai_dir/src/Foo.cs
( cd ai_dir && git add src/Foo.cs )

# 证据: AI 写 60 行 (75% -> heavy)
cat > ai_dir/.governance/ai-evidence.jsonl <<EOF
{"ts":"${TODAY}T10:00:00Z","tool":"claude-code","model":"opus-4","file":"src/Foo.cs","added":60,"removed":0}
EOF
( cd ai_dir && python3 "$COLLECT" --staged --trailer-only 2>/dev/null ) | grep -q "AI-Usage: heavy"
expect "collect 75%占比判定heavy" 0 $?

# 证据: AI 写 8 行 (10% -> light)
cat > ai_dir/.governance/ai-evidence.jsonl <<EOF
{"ts":"${TODAY}T10:00:00Z","tool":"claude-code","model":"opus-4","file":"src/Foo.cs","added":8,"removed":0}
EOF
( cd ai_dir && python3 "$COLLECT" --staged --trailer-only 2>/dev/null ) | grep -q "AI-Usage: light"
expect "collect 10%占比判定light" 0 $?

# 仅工具标记无行数 -> used
cat > ai_dir/.governance/ai-evidence.jsonl <<EOF
{"ts":"${TODAY}T10:00:00Z","tool":"cursor"}
EOF
( cd ai_dir && python3 "$COLLECT" --staged --trailer-only 2>/dev/null ) | grep -q "AI-Usage: used"
expect "collect 仅工具标记判定used" 0 $?

# 无证据 -> none
rm -f ai_dir/.governance/ai-evidence.jsonl
( cd ai_dir && python3 "$COLLECT" --staged --trailer-only 2>/dev/null ) | grep -q "AI-Usage: none"
expect "collect 无证据判定none" 0 $?

# .sh / .yml 等脚本配置类文件也应计入 AI-Usage (改 CI/脚本的 PR 不被漏统计)
( cd ai_dir && git checkout -q -- . 2>/dev/null; printf 'echo hi\n' > deploy.sh && git add deploy.sh )
cat > ai_dir/.governance/ai-evidence.jsonl <<EOF
{"ts":"${TODAY}T10:00:00Z","tool":"claude-code","model":"m","file":"deploy.sh","added":1,"removed":0}
EOF
( cd ai_dir && python3 "$COLLECT" --staged --trailer-only 2>/dev/null ) | grep -qE "AI-Usage: (light|heavy|medium)"
expect "collect 识别 .sh 脚本文件" 0 $?
( cd ai_dir && git reset -q HEAD deploy.sh; rm -f deploy.sh .governance/ai-evidence.jsonl )

# == validate_mr.py 从 commit trailer 读 AI-Usage ==
echo
echo "== validate_mr.py × commit trailer =="

# 在 ai_dir 里造一个带 AI-Usage trailer 的提交
( cd ai_dir && git commit -qm "feat: foo

AI-Usage: heavy
AI-Tools: claude-code" )

# MR 描述故意不含 AI-Usage (新规范: 不手填), 但 commit trailer 有 -> 通过
cat > ai_dir/mr_no_ai.md <<'EOF'
## 背景
加功能。
## 变更内容
- 改了 Foo
## 自测确认
- [x] build
EOF
( cd ai_dir && python3 "$VAL" --file mr_no_ai.md --config "${WORK}/soft.yml" --diff-base HEAD~1 ) >/dev/null 2>&1
expect "trailer有AI-Usage则描述无也通过" 0 $?

# == check_tested.py / record_test_run.py ==
echo
echo "== check_tested.py =="
CHECK="${SCRIPT_DIR}/check_tested.py"
RECORD="${SCRIPT_DIR}/record_test_run.py"

mkdir -p ct_dir/.governance ct_dir/src ct_dir/tests
( cd ct_dir && git init -q && git config user.email t@t && git config user.name t \
  && echo seed > seed.txt && git add seed.txt && git commit -qm seed )

# 改动生产代码, 无测试痕迹, 软模式 -> WARN exit0
echo "public class Foo { public int Bar()=>1; }" > ct_dir/src/Foo.cs
( cd ct_dir && git add src/Foo.cs && git diff --cached --unified=0 --no-color > d.diff )
( cd ct_dir && python3 "$CHECK" --diff-file d.diff --evidence .governance/test-evidence.jsonl --soft ) >/dev/null 2>&1
expect "改生产码无测试软模式WARN(exit0)" 0 $?

# risk:untested 注解豁免 -> 放行
cat > ct_dir/src/Foo.cs <<'EOF'
// risk:untested reason:"纯DTO无业务逻辑，集成测试间接覆盖" owner:@team reviewed:TODAY_PLACEHOLDER
public class Foo { public int Bar()=>1; }
EOF
sed -i "s/TODAY_PLACEHOLDER/${TODAY}/" ct_dir/src/Foo.cs
( cd ct_dir && git add src/Foo.cs && git diff --cached --unified=0 --no-color > d.diff )
( cd ct_dir && python3 "$CHECK" --diff-file d.diff --evidence .governance/test-evidence.jsonl ) >/dev/null 2>&1
expect "risk:untested注解豁免放行" 0 $?

# record_test_run 跑全绿 + 改测试文件 -> 放行
cat > ct_dir/src/Foo.cs <<'EOF'
public class Foo { public int Bar()=>1; }
EOF
echo "public class FooTests { public void T(){} }" > ct_dir/tests/FooTests.cs
( cd ct_dir && git add src/Foo.cs tests/FooTests.cs && git diff --cached --unified=0 --no-color > d.diff )
( cd ct_dir && python3 "$RECORD" -- "$BASH_BIN" -c 'echo "Passed!  - Failed: 0, Passed: 5"' ) >/dev/null 2>&1
( cd ct_dir && python3 "$CHECK" --diff-file d.diff --evidence .governance/test-evidence.jsonl ) >/dev/null 2>&1
expect "全绿记录+改测试文件放行" 0 $?

# 失败测试记录 -> 无条件硬拦 exit1
( cd ct_dir && python3 "$RECORD" -- "$BASH_BIN" -c 'echo "Failed: 2, Passed: 3"; exit 1' ) >/dev/null 2>&1
( cd ct_dir && python3 "$CHECK" --diff-file d.diff --evidence .governance/test-evidence.jsonl ) >/dev/null 2>&1
expect "失败测试记录硬拦(exit1)" 1 $?

# 同命令修复后全绿 -> 不再硬拦 (只看每命令最新)
( cd ct_dir && python3 "$RECORD" -- "$BASH_BIN" -c 'echo "Failed: 2, Passed: 3"; exit 1' ) >/dev/null 2>&1
sleep 1
# 注意: record 用相同 cmd 文本, 最新一条覆盖旧的失败
CURRENT_STATE="$(cd ct_dir && PYTHONPATH="$SCRIPT_DIR" python3 -c 'from governance_common import repository_state; print(repository_state())')"
cat >> ct_dir/.governance/test-evidence.jsonl <<EOF
{"ts":"2099-01-01T00:00:00Z","cmd":"bash -c echo \\"Failed: 2, Passed: 3\\"; exit 1","exit_code":0,"total":5,"passed":5,"failed":0,"covers":[],"git_state":"${CURRENT_STATE}"}
EOF
python3 "$CHECK" --diff-file ct_dir/d.diff --evidence ct_dir/.governance/test-evidence.jsonl >/dev/null 2>&1
expect "同命令修复后全绿不再硬拦" 0 $?

# record_test_run 透传退出码: 被包装命令失败则脚本也非0
( cd ct_dir && python3 "$RECORD" -- "$BASH_BIN" -c 'exit 3' ) >/dev/null 2>&1
expect "record_test_run 透传退出码" 3 $?

# == check_tested.py × Tested trailer (CI 退路) ==
echo
echo "== check_tested.py × Tested trailer =="
mkdir -p tr_dir/src tr_dir/tests
( cd tr_dir && git init -q && git config user.email t@t && git config user.name t \
  && echo seed > seed.txt && git add seed.txt && git commit -qm seed )
echo "public class Bar { public int Go()=>1; }" > tr_dir/src/Bar.cs
echo "public class BarTests { public void T(){} }" > tr_dir/tests/BarTests.cs
( cd tr_dir && git add -A && git commit -qm "feat: bar

Tested: pass (5/5)" )
# 无本地证据, 靠 commit trailer + 改了测试文件 -> 放行
( cd tr_dir && python3 "$CHECK" --diff-base HEAD~1 --evidence .governance/none.jsonl ) >/dev/null 2>&1
expect "Tested:pass trailer+改测试放行" 0 $?

# Tested: fail trailer -> 硬拦
echo "public class Baz { public int Go()=>1; }" > tr_dir/src/Baz.cs
( cd tr_dir && git add -A && git commit -qm "feat: baz

Tested: fail" )
( cd tr_dir && python3 "$CHECK" --diff-base HEAD~1 --evidence .governance/none.jsonl ) >/dev/null 2>&1
expect "Tested:fail trailer硬拦(exit1)" 1 $?

# == create_mr.py ==
echo
echo "== create_mr.py =="
CREATE="${SCRIPT_DIR}/create_mr.py"

mkdir -p mr_dir/src mr_dir/tests mr_dir/.governance
( cd mr_dir && git init -q && git config user.email t@t && git config user.name t \
  && echo seed > seed.txt && git add seed.txt && git commit -qm seed \
  && git checkout -qb feat/x )
printf 'public class Pay { public int C()=>1; }\n' > mr_dir/src/Pay.cs
printf 'public class PayTests { public void T(){} }\n' > mr_dir/tests/PayTests.cs
( cd mr_dir && git add -A && git commit -qm "feat: add pay

AI-Usage: medium
AI-Tools: claude-code
Tested: pass (15/15)" )
MR_STATE="$(cd mr_dir && PYTHONPATH="$SCRIPT_DIR" python3 -c 'from governance_common import repository_state; print(repository_state())')"
cat > mr_dir/.governance/test-evidence.jsonl <<EOF
{"ts":"2026-06-26T12:00:00Z","cmd":"dotnet test","exit_code":0,"total":15,"passed":15,"failed":0,"covers":[],"git_state":"${MR_STATE}"}
EOF

# dry-run 生成描述, 退出 0
( cd mr_dir && python3 "$CREATE" --why "用户需求:接入支付" --target-branch master --dry-run ) >/tmp/mr_out.txt 2>&1
expect "create_mr dry-run 退出0" 0 $?

# 描述含 4 个必填段落
grep -q "## 背景" /tmp/mr_out.txt && grep -q "## 变更内容" /tmp/mr_out.txt \
  && grep -q "## 自测确认" /tmp/mr_out.txt && grep -q "## 风险与回滚" /tmp/mr_out.txt
expect "描述含4个必填段落" 0 $?

# 背景来自 --why
grep -q "用户需求:接入支付" /tmp/mr_out.txt
expect "背景填入--why内容" 0 $?

# 变更内容自动列出改动文件
grep -q "src/Pay.cs" /tmp/mr_out.txt
expect "变更内容自动列文件" 0 $?

# 自测确认从测试证据自动生成 (含 15/15)
grep -q "15/15" /tmp/mr_out.txt
expect "自测确认含测试结果" 0 $?

# AI 元数据折叠块含 AI-Usage
grep -q "AI-Usage.*medium" /tmp/mr_out.txt && grep -q "<details>" /tmp/mr_out.txt
expect "AI元数据在折叠块" 0 $?

# 无 --why 且非 dry-run -> 用法错误 exit2
( cd mr_dir && python3 "$CREATE" --target-branch master ) >/dev/null 2>&1
expect "无--why非dry-run报错(exit2)" 2 $?

# 敏感路径自动评估为大变更
mkdir -p mr_dir/ci
echo "x: 1" > mr_dir/ci/deploy.yml
( cd mr_dir && git add -A && git commit -qm "ci: deploy

Tested: pass (1/1)" )
( cd mr_dir && python3 "$CREATE" --why "改CI" --target-branch master --dry-run ) >/tmp/mr_out2.txt 2>&1
grep -q "高敏路径" /tmp/mr_out2.txt
expect "敏感路径自动评估大变更" 0 $?

# == create_mr.py × DeliverHQ 需求文档 ==
echo
echo "== create_mr.py × DeliverHQ =="
mkdir -p dh_dir/src
( cd dh_dir && git init -q && git config user.email t@t && git config user.name t \
  && echo seed > seed.txt && git add seed.txt && git commit -qm seed \
  && git checkout -qb feat/CR-1234-pay )
mkdir -p dh_dir/DeliverHQ/change-requests/CR-1234
cat > dh_dir/DeliverHQ/change-requests/CR-1234/requirement.md <<'EOF'
# CR-1234 微信支付

## 背景
运营反馈用户强烈要求微信支付，当前只有支付宝，流失明显。

## 验收
- 完成一笔支付
EOF
cat > dh_dir/governance.config.yml <<'EOF'
deliverhq_integration:
  enabled: true
  records_dirs: ["DeliverHQ/change-requests/"]
  requirement_doc_patterns: ["requirement.md"]
  background_headings: ["背景", "Background"]
EOF
printf 'public class Pay{public int C()=>1;}\n' > dh_dir/src/Pay.cs
( cd dh_dir && git add -A && git commit -qm "feat: pay" )

# 不传 --why, 分支名含 CR-1234 -> 自动从需求文档读背景
( cd dh_dir && python3 "$CREATE" --target-branch master --dry-run ) >/tmp/dh_out.txt 2>&1
expect "DeliverHQ自动读背景 dry-run退出0" 0 $?
grep -q "运营反馈用户强烈要求微信支付" /tmp/dh_out.txt
expect "背景取自需求文档内容" 0 $?
grep -q "Requirement-ID: CR-1234" /tmp/dh_out.txt
expect "关联自动补需求ID" 0 $?

# DeliverHQ 启用但需求文档不存在 -> 回退要求 --why (exit2)
( cd dh_dir && git checkout -qb feat/CR-9999-x && python3 "$CREATE" --target-branch master ) >/dev/null 2>&1
expect "需求文档缺失回退要求--why(exit2)" 2 $?

echo
# == custom_patterns (公司自定义规则, 在 cust_dir 内运行以便读源文件) ==
echo
echo "== custom_patterns =="
CDATE="$(date +%Y-%m-%d)"
rm -rf cust_dir && mkdir -p cust_dir
( cd cust_dir && git init -q && git config user.email t@t && git config user.name t && echo base > b.txt && git add b.txt && git commit -qm base )
cat > cust_dir/cfg.yml <<'CFG'
risk_annotations:
  enforcement: hard
  reviewed_max_age_days: 36500
  registered_types: [my-unsafe-query]
  custom_patterns:
    - type: my-unsafe-query
      regex: 'UnsafeQuery\s*\('
      desc: "no UnsafeQuery"
CFG
echo 'void f(){ UnsafeQuery("x"); }' > cust_dir/bad.cs
( cd cust_dir && git add bad.cs && git diff --cached --unified=0 --no-color -- bad.cs > bad.diff
  python3 "$SCAN" --diff-file bad.diff --config cfg.yml >/dev/null 2>&1 ); expect "自定义规则命中应拦截" 1 $?
{ printf '// risk:my-unsafe-query reason:"legacy readonly report query reviewed safe" owner:@team reviewed:%s\n' "$CDATE"; echo 'void g(){ UnsafeQuery("y"); }'; } > cust_dir/good.cs
( cd cust_dir && git add good.cs && git diff --cached --unified=0 --no-color -- good.cs > good.diff
  python3 "$SCAN" --diff-file good.diff --config cfg.yml >/dev/null 2>&1 ); expect "自定义规则加注解应放行" 0 $?

# ============================================================
# 多人协作 diff-base 回归测试
# 复现: 你从 main 建分支改自己的文件, 期间同事往 main 提交并被你 merge 进来,
# 检查时不应把同事的改动算进"你要负责的 diff"。
# ============================================================
echo
echo "== 多人协作 diff-base =="
CHECK="${SCRIPT_DIR}/check_tested.py"
COLLAB="$(mktemp -d)"
(
  cd "$COLLAB"
  git init -q; git config user.email t@t; git config user.name t; git config init.defaultBranch main
  echo "class App {}" > app.cs; git add app.cs; git commit -qm init; git branch -M main
  # 你建分支, 改自己的文件并写了测试
  git checkout -q -b feature
  echo "class MyFeature {}" > myfeature.cs
  echo "class MyFeatureTests {}" > MyFeatureTests.cs
  git add -A; git commit -qm "feat: my work with test"
  # 同事往 main 提交一个"未测的生产代码文件"
  git checkout -q main
  echo "class ColleagueService { void Pay(){} }" > colleague.cs
  git add colleague.cs; git commit -qm "feat: colleague work (no test)"
  # 你把 main 最新代码 merge 进自己分支
  git checkout -q feature
  git merge -q main -m "merge main"
  # 模拟 CI 修复后行为: diff-base = 目标分支最新 tip (main)
  python3 "$CHECK" --diff-base main >/dev/null 2>&1
)
expect "merge他人代码后只检查自己的改动(不误拦)" 0 $?

# 反向验证: 硬模式下自己改了生产代码却没测, 仍应拦 (证明修复没把门禁弄失效)
if [[ "$HAS_YAML" == "1" ]]; then
  COLLAB2="$(mktemp -d)"
  (
    cd "$COLLAB2"
    git init -q; git config user.email t@t; git config user.name t; git config init.defaultBranch main
    echo "class App {}" > app.cs; git add app.cs; git commit -qm init; git branch -M main
    cat > cfg.yml <<'CFG'
testing:
  enforcement: hard
  exclude_paths: []
CFG
    git checkout -q -b feature
    echo "class MyService { void DoWork(){} }" > myservice.cs   # 生产代码, 无测试
    git add myservice.cs; git commit -qm "feat: untested work"
    python3 "$CHECK" --diff-base main --config cfg.yml >/dev/null 2>&1
  )
  expect "硬模式自己改生产代码没测应拦截" 1 $?
else
  skip "硬模式自己改生产代码没测应拦截"
fi

# ============================================================
# 多人协作阻碍修复 回归测试 (P0/P1/P2 八项)
# ============================================================
echo
echo "== 协作阻碍修复回归 =="
SCANP="${SCRIPT_DIR}/scan_risks.py"
CHK="${SCRIPT_DIR}/check_tested.py"

# [P0-vendored] 生成/引入代码路径豁免: vendor 下命中风险也不拦
VD="$(mktemp -d)"
( cd "$VD" && git init -q && git config user.email t@t && git config user.name t && git config init.defaultBranch main
  echo "x" > seed.txt && git add -A && git commit -qm init && git branch -M main
  cat > hard.yml <<'CFG'
risk_annotations:
  enforcement: hard
  scan_exclude_paths: ["**/vendor/**", "**/*.generated.*"]
  registered_types: [auth-bypass]
CFG
  git checkout -q -b feat
  mkdir -p vendor/lib
  echo 'bool f(string userId){ return userId == "626786582b50ab8ec08b0fa0"; }' > vendor/lib/dep.cs
  git add vendor/lib/dep.cs
  git diff --cached -w --unified=0 --no-color > v.diff
  python3 "$SCANP" --diff-file v.diff --config hard.yml >/dev/null 2>&1 )
expect "P0 vendor路径豁免不拦" 0 $?

# [P0-default-soft] 默认无 config = 软, 命中风险只警告不拦
SD="$(mktemp -d)"
( cd "$SD" && git init -q && git config user.email t@t && git config user.name t
  echo 'bool f(string u){ return u == "626786582b50ab8ec08b0fa0"; }' > a.cs
  git add a.cs && git commit -qm x >/dev/null 2>&1
  git diff --unified=0 --no-color "$EMPTY_TREE" HEAD -- a.cs > a.diff
  python3 "$SCANP" --diff-file a.diff >/dev/null 2>&1 )
expect "P0 默认soft命中风险不拦" 0 $?

# [P1-preexisting] -w 忽略空格: 只重缩进他人风险行不误报
WS="$(mktemp -d)"
( cd "$WS" && git init -q && git config user.email t@t && git config user.name t && git config init.defaultBranch main
  printf 'class S {
bool f(string u){ return u == "626786582b50ab8ec08b0fa0"; }
}
' > s.cs
  git add s.cs && git commit -qm init && git branch -M main
  git checkout -q -b feat
  # 只加缩进(空格), 内容不变
  printf 'class S {
    bool f(string u){ return u == "626786582b50ab8ec08b0fa0"; }
}
' > s.cs
  git add s.cs && git commit -qm reindent
  cat > hard.yml <<'CFG'
risk_annotations:
  enforcement: hard
  registered_types: [auth-bypass]
CFG
  python3 "$SCANP" --diff-base main --config hard.yml >/dev/null 2>&1 )
expect "P1 重缩进他人风险行不误报(-w)" 0 $?

# [P1-rename] 纯重命名文件不要求补测试
RN="$(mktemp -d)"
( cd "$RN" && git init -q && git config user.email t@t && git config user.name t && git config init.defaultBranch main
  echo 'class Svc { void Pay(){} }' > svc.cs && git add svc.cs && git commit -qm init && git branch -M main
  cat > hard.yml <<'CFG'
testing:
  enforcement: hard
  exclude_paths: []
CFG
  git checkout -q -b feat
  git mv svc.cs service.cs && git commit -qm "rename svc"
  python3 "$CHK" --diff-base main --config hard.yml >/dev/null 2>&1 )
expect "P1 纯重命名不要求测试" 0 $?

# [P1-squash] 目标分支 squash 前进后, 本分支只检查自己真实改动
SQ="$(mktemp -d)"
( cd "$SQ" && git init -q && git config user.email t@t && git config user.name t && git config init.defaultBranch main
  echo "base" > app.cs && git add app.cs && git commit -qm init && git branch -M main
  cat > hard.yml <<'CFG'
testing:
  enforcement: hard
  exclude_paths: []
CFG
  git checkout -q -b feat
  echo 'class Mine { void Go(){} }' > mine.cs
  echo 'class MineTests {}' > mineTests.cs
  git add -A && git commit -qm "my work + test

Tested: pass (2/2)"
  # 同事在 main 加未测生产文件
  git checkout -q main
  echo 'class Other { void Do(){} }' > other.cs && git add other.cs && git commit -qm "colleague"
  git checkout -q feat && git merge -q main -m merge
  python3 "$CHK" --diff-base main --config hard.yml >/dev/null 2>&1 )
expect "P1 merge他人未测代码不拦自己" 0 $?

# [自指] 扫描器脚本目录豁免: 业务仓库自包含副本改动 governance/scripts/ 不被自己扫
SELF="$(mktemp -d)"
( cd "$SELF" && git init -q && git config user.email t@t && git config user.name t && git config init.defaultBranch main
  echo seed > seed.txt && git add -A && git commit -qm init && git branch -M main
  git checkout -q -b feat
  mkdir -p governance/scripts
  # 造一个含风险模式字面示例的"脚本副本"(模拟 scan_risks.py 里的 catch{} 注释)
  printf '# rule example: catch { }\nPATTERN = "catch"\n' > governance/scripts/scan_risks.py
  git add governance/scripts/scan_risks.py
  git diff --cached -w --unified=0 --no-color > s.diff
  cat > cfg.yml <<'CFG'
risk_annotations:
  enforcement: hard
  scan_exclude_paths: ["**/governance/scripts/**", "governance/scripts/**"]
  registered_types: [swallowed-exception]
CFG
  python3 "$SCANP" --diff-file s.diff --config cfg.yml >/dev/null 2>&1 )
expect "扫描器脚本目录豁免(不自指误报)" 0 $?

# 已有 prepare-commit-msg 必须被链式调用, 不能只备份后弃用
echo
echo "== install-hooks.sh =="
HOOK_REPO="$(mktemp -d)"
HOOK_INSTALL="${SCRIPT_DIR}/install-hooks.sh"
(
  cd "$HOOK_REPO"
  git init -q
  mkdir -p .git/hooks
  cat > .git/hooks/prepare-commit-msg <<'LEGACY'
#!/usr/bin/env bash
printf 'legacy-called\n' > "${1}.legacy"
LEGACY
  chmod +x .git/hooks/prepare-commit-msg
  bash "$HOOK_INSTALL" >/dev/null
  printf 'subject\n' > message.txt
  .git/hooks/prepare-commit-msg message.txt
  test -f message.txt.legacy
)
expect "安装后继续调用已有 prepare-commit-msg" 0 $?

echo "============================================"
echo " 通过 $PASS / 失败 $FAIL"
echo "============================================"
[[ $FAIL -eq 0 ]]
