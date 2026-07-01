#!/usr/bin/env python3
"""
scan_risks.py — MR 治理规范 v1 风险扫描器（硬门禁）

扫描 git diff 中新增/修改的代码行, 匹配已注册的风险模式;
命中的代码上方 5 行内必须有合法的 risk: 注解, 否则退出码非 0。

用法:
    python scan_risks.py --diff-base origin/master
    python scan_risks.py --diff-base HEAD~1 --config governance.config.yml
    python scan_risks.py --staged          # 扫描已暂存改动 (pre-commit 钩子用)

退出码:
    0  无风险, 或全部命中点都有合法注解
    1  存在命中点缺少合法注解 (硬阻断)
    2  运行错误 (git 不可用 / 配置缺失等)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys

# ---------- 可选依赖 pyyaml, 缺失时退化为内置默认 ----------
try:
    import yaml  # type: ignore
    _HAS_YAML = True
except Exception:  # pragma: no cover
    _HAS_YAML = False


# ============================================================
# 默认配置 (无 governance.config.yml 时使用)
# ============================================================
DEFAULT_CONFIG = {
    "risk_annotations": {
        "enforcement": "soft",  # 默认软启动(只警告); 团队显式配 hard 才硬拦
        "reviewed_max_age_days": 180,
        # 路径豁免: 生成/引入/第三方代码不扫 (开发者不为这些代码负责)
        "scan_exclude_paths": [
            "**/vendor/**", "**/node_modules/**", "**/third_party/**",
            "**/*_pb2.py", "**/*_pb2_grpc.py", "**/*.pb.go",
            "**/*.generated.*", "**/gen/**", "**/dist/**", "**/build/**",
            "**/migrations/**", "**/*.min.js", "**/*.min.css",
        ],
        "registered_types": [
            "auth-bypass",
            "magic-id",
            "swallowed-exception",
            "suppressed-warning",
            "skipped-test",
            "time-bypass",
            "env-hardcode",
            "todo-no-context",
            "test-removal",
        ],
        "reason_blacklist": [
            "临时", "先这样", "历史原因", "TODO", "待确认",
            "quick fix", "temp", "wip", "hack", "for now",
        ],
    },
}

# 注解里要求的最短 reason 长度 (字符数)
MIN_REASON_LEN = 10
# 命中行上方回看的行数
ANNOTATION_LOOKBACK = 5

# 只扫描这些扩展名的源码文件 (其余跳过, 避免误报二进制/数据文件)
SCAN_EXTENSIONS = {
    ".cs", ".js", ".ts", ".jsx", ".tsx", ".java", ".go",
    ".py", ".rb", ".php", ".cpp", ".cc", ".c", ".h", ".hpp",
    ".kt", ".rs", ".scala", ".swift",
}


# ============================================================
# 风险模式定义
# 每个模式: (类型名, 编译后的正则, 说明)
# 这些正则有意保守 —— 宁可少报也尽量减少误报, 真正的精确度靠 reason 注解兜底
# ============================================================
def _build_patterns() -> list[tuple[str, re.Pattern, str]]:
    p: list[tuple[str, re.Pattern, str]] = []

    # 1. auth-bypass: 认证相关字段与字面量字符串比较
    #    userId == "xxx" / role == "admin" / adminUserId.Equals("...")
    p.append((
        "auth-bypass",
        re.compile(
            r'(?i)\b(\w*(?:user|admin|role|account|uid|owner|tenant|auth)\w*)\b'
            r'\s*(?:==|!=|\.Equals\s*\()\s*"[^"]+"'
        ),
        "认证字段与字面量字符串比较",
    ))

    # 2. magic-id: 业务代码硬编码 ObjectId(24 hex) / UUID / >=12 位连续数字
    p.append((
        "magic-id",
        re.compile(
            r'"(?:[0-9a-fA-F]{24}'                              # mongo ObjectId
            r'|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'   # uuid
            r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
            r'|\d{12,})"'                                        # 长数字串
        ),
        "硬编码 ObjectId / UUID / 长数字 ID",
    ))

    # 3. swallowed-exception: 空 catch 块 (同行)
    #    catch { }  /  catch (Exception) { }  /  catch (e) {}
    p.append((
        "swallowed-exception",
        re.compile(r'\bcatch\b[^{]*\{\s*\}'),
        "catch 块为空 (吞异常)",
    ))

    # 4. suppressed-warning: 各类静态检查抑制指令
    p.append((
        "suppressed-warning",
        re.compile(
            r'(#pragma\s+warning\s+disable'
            r'|\[\s*SuppressMessage'
            r'|//\s*nolint'
            r'|//\s*eslint-disable'
            r'|#\s*noqa'
            r'|//\s*@ts-ignore)'
        ),
        "静态检查抑制指令",
    ))

    # 5. skipped-test: 跳过/忽略测试
    p.append((
        "skipped-test",
        re.compile(
            r'(\[\s*Fact\s*\(\s*Skip\s*='
            r'|\[\s*Theory\s*\(\s*Skip\s*='
            r'|\[\s*Ignore'
            r'|\bit\.skip\b|\bxit\b|\bdescribe\.skip\b'
            r'|@pytest\.mark\.skip)'
        ),
        "跳过或忽略测试",
    ))

    # 6. time-bypass: 当前时间与字面量日期比较
    p.append((
        "time-bypass",
        re.compile(
            r'(?i)(DateTime\.(?:Now|UtcNow|Today)|time\.Now\(\)|Date\.now\(\))'
            r'.{0,40}?(?:[<>]=?|==)'
            r'.{0,20}?(?:new\s+DateTime\s*\(\s*\d{4}|"\d{4}-\d{2}-\d{2}")'
        ),
        "当前时间与字面量日期比较",
    ))

    # 7. env-hardcode: 环境字符串硬编码判断
    p.append((
        "env-hardcode",
        re.compile(
            r'(?i)(?:env|environment|aspnetcore_environment|node_env)'
            r'\w*\s*(?:==|!=|\.Equals\s*\()\s*"'
            r'(?:prod|production|dev|development|staging|test|local)"'
        ),
        "环境字符串硬编码判断行为",
    ))

    # 8. todo-no-context: TODO/FIXME/HACK 不含 (owner, date)
    #    合规示例: // TODO(@alice, 2026-08-01): ...
    p.append((
        "todo-no-context",
        re.compile(r'(?i)\b(TODO|FIXME|HACK)\b(?!\s*\(\s*@?\w+\s*,\s*\d{4}-\d{2}-\d{2})'),
        "TODO/FIXME/HACK 缺少 (owner, 日期)",
    ))

    return p


PATTERNS = _build_patterns()


def build_custom_patterns(cfg: dict) -> list:
    """读取 config 的 risk_annotations.custom_patterns, 编译公司自定义规则。
    每条: {type, regex, desc}。正则无效则跳过并警告, 不中断扫描。"""
    out = []
    ra = cfg.get("risk_annotations", {}) if isinstance(cfg, dict) else {}
    for item in (ra.get("custom_patterns") or []):
        t = (item or {}).get("type")
        rx = (item or {}).get("regex")
        desc = (item or {}).get("desc") or t
        if not t or not rx:
            continue
        try:
            out.append((t, re.compile(rx), desc))
        except re.error as e:
            sys.stderr.write(f"[scan-risks] custom_pattern {t} regex invalid, skipped: {e}\n")
    return out


# ============================================================
# 配置加载
# ============================================================
def load_config(path: str | None) -> dict:
    if not path:
        # 尝试默认位置
        for cand in ("governance.config.yml", "governance.config.yaml"):
            if os.path.isfile(cand):
                path = cand
                break
    if not path or not os.path.isfile(path):
        return DEFAULT_CONFIG
    if not _HAS_YAML:
        sys.stderr.write(
            "[scan-risks] 警告: 未安装 pyyaml, 忽略 config 文件, 使用内置默认。\n"
        )
        return DEFAULT_CONFIG
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:  # pragma: no cover
        sys.stderr.write(f"[scan-risks] 警告: 解析 {path} 失败 ({e}), 使用默认。\n")
        return DEFAULT_CONFIG
    # 合并: config 缺失字段用默认补齐
    merged = {**DEFAULT_CONFIG, **data}
    ra = {**DEFAULT_CONFIG["risk_annotations"], **(data.get("risk_annotations") or {})}
    merged["risk_annotations"] = ra
    return merged


# ============================================================
# git 交互
# ============================================================
def run_git(args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            check=True, capture_output=True, text=True,
        )
        return out.stdout
    except FileNotFoundError:
        sys.stderr.write("[scan-risks] 错误: 找不到 git。\n")
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"[scan-risks] git 命令失败: git {' '.join(args)}\n{e.stderr}\n")
        sys.exit(2)


def get_diff(diff_base: str | None, staged: bool) -> str:
    # -w 忽略空白改动: 重缩进/换行包裹触碰他人既有风险行时不误报为新增
    if staged:
        return run_git(["diff", "--cached", "-w", "--unified=0", "--no-color"])
    if diff_base:
        return run_git(["diff", f"{diff_base}...HEAD", "-w", "--unified=0", "--no-color"])
    # 默认: 与上一个提交比
    return run_git(["diff", "HEAD~1...HEAD", "-w", "--unified=0", "--no-color"])


# ============================================================
# diff 解析
# 返回: { 文件路径: [(行号, 行内容), ...] }  只含新增行 (+ 开头)
# ============================================================
_HUNK_RE = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')


def parse_diff(diff_text: str) -> dict[str, list[tuple[int, str]]]:
    result: dict[str, list[tuple[int, str]]] = {}
    cur_file: str | None = None
    new_lineno = 0

    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            # +++ b/path/to/file  或  +++ /dev/null
            path = line[4:].strip()
            if path == "/dev/null":
                cur_file = None
            else:
                # 去掉前缀 b/
                cur_file = path[2:] if path.startswith(("a/", "b/")) else path
                result.setdefault(cur_file, [])
            continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if m:
                new_lineno = int(m.group(1))
            continue
        if cur_file is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            # 新增行
            result[cur_file].append((new_lineno, line[1:]))
            new_lineno += 1
        elif line.startswith("-") and not line.startswith("---"):
            # 删除行不增加 new_lineno
            pass
        else:
            # 上下文行 (unified=0 时一般没有, 但保险)
            new_lineno += 1
    return result


# ============================================================
# 注解校验
# ============================================================
_RISK_INLINE_RE = re.compile(
    r'risk:\s*(?P<type>[\w-]+)'
    r'.*?reason:\s*"(?P<reason>[^"]*)"'
    r'.*?owner:\s*(?P<owner>@?[\w/.-]+)'
    r'.*?reviewed:\s*(?P<reviewed>\d{4}-\d{2}-\d{2})',
    re.IGNORECASE | re.DOTALL,
)

# 多行块字段解析
_BLOCK_FIELD_RE = {
    "type": re.compile(r'type:\s*([\w-]+)', re.IGNORECASE),
    "reason": re.compile(r'reason:\s*(.+)', re.IGNORECASE),
    "owner": re.compile(r'owner:\s*(@?[\w/.-]+)', re.IGNORECASE),
    "reviewed": re.compile(r'reviewed:\s*(\d{4}-\d{2}-\d{2})', re.IGNORECASE),
}


def _read_file_lines(path: str) -> list[str] | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return None


def _validate_annotation_fields(
    risk_type: str,
    reason: str,
    reviewed: str,
    expected_types: set[str],
    cfg: dict,
) -> list[str]:
    """
    返回问题列表; 空列表表示注解合法。
    expected_types: 该命中行涉及的所有风险类型集合。注解类型只要属于其中之一即视为匹配
    (一行可能同时命中多个模式, 如认证比较的字符串同时是 ObjectId)。
    """
    problems: list[str] = []
    ra = cfg["risk_annotations"]

    # 类型必须已注册
    if risk_type not in ra["registered_types"]:
        problems.append(f'风险类型 "{risk_type}" 未注册')
    # 类型应覆盖该行命中类型之一
    if risk_type not in expected_types:
        problems.append(
            f'注解类型 "{risk_type}" 不匹配该行命中类型 '
            f'({"/".join(sorted(expected_types))})'
        )

    # reason 长度
    if len(reason.strip()) < MIN_REASON_LEN:
        problems.append(f'reason 过短 (<{MIN_REASON_LEN} 字)')
    # reason 黑名单词
    low = reason.lower()
    for bad in ra["reason_blacklist"]:
        if bad.lower() in low:
            problems.append(f'reason 含黑名单词 "{bad}"')
            break

    # reviewed 日期有效 + 未过期
    try:
        rev = dt.date.fromisoformat(reviewed)
        age = (dt.date.today() - rev).days
        max_age = int(ra.get("reviewed_max_age_days", 180))
        if age > max_age:
            # 过期是软提醒: 继承来的他人注解不应因日期年龄卡死协作
            problems.append(f'[warn] reviewed 已过期 ({age} 天 > {max_age} 天), 建议复查更新')
        elif age < 0:
            problems.append("[warn] reviewed 日期在未来")
    except ValueError:
        problems.append(f'reviewed 日期格式非法 "{reviewed}"')

    return problems


def find_annotation(
    lines: list[str],
    hit_lineno: int,
    expected_types: set[str],
    cfg: dict,
) -> tuple[bool, list[str]]:
    """
    在 hit_lineno (1-based) 上方 ANNOTATION_LOOKBACK 行内查找注解。
    expected_types: 该行命中的所有风险类型, 注解类型匹配其一即可。
    返回 (是否找到并合法, 问题列表)。
    """
    start = max(0, hit_lineno - 1 - ANNOTATION_LOOKBACK)
    end = hit_lineno - 1  # 不含命中行本身
    window = lines[start:end]
    window_text = "\n".join(window)

    # 先试单行格式
    m = _RISK_INLINE_RE.search(window_text)
    if m:
        problems = _validate_annotation_fields(
            m.group("type").lower(),
            m.group("reason"),
            m.group("reviewed"),
            expected_types,
            cfg,
        )
        return (len(problems) == 0, problems)

    # 再试多行块 risk-begin ... risk-end
    if "risk-begin" in window_text and "risk-end" in window_text:
        fields = {}
        for key, rx in _BLOCK_FIELD_RE.items():
            fm = rx.search(window_text)
            if fm:
                fields[key] = fm.group(1).strip().strip('"')
        missing = [k for k in ("type", "reason", "owner", "reviewed") if k not in fields]
        if missing:
            return (False, [f"多行注解缺字段: {', '.join(missing)}"])
        problems = _validate_annotation_fields(
            fields["type"].lower(),
            fields["reason"],
            fields["reviewed"],
            expected_types,
            cfg,
        )
        return (len(problems) == 0, problems)

    return (False, ["上方 5 行内未找到 risk: 注解"])


# ============================================================
# test-removal: 检测被删除的测试
# ============================================================
_TEST_DECL_RE = re.compile(
    r'(\[\s*Fact\b|\[\s*Theory\b|\[\s*Test\b'
    r'|\bdef\s+test_\w+'
    r'|\bit\s*\(|\btest\s*\(|@Test\b)'
)


def check_test_removal(diff_text: str, cfg: dict) -> list[str]:
    """
    检测 diff 里删除的测试声明。若有删除且 commit/描述里没有
    risk:test-removal 注解, 返回问题。这里只检测删除行, 注解需在 commit message
    或同一 diff 的新增注解里 —— v1 简化: 只要 diff 文本里出现合法 test-removal 注解即放行。
    """
    removed_tests = []
    for line in diff_text.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            if _TEST_DECL_RE.search(line):
                removed_tests.append(line[1:].strip())
    if not removed_tests:
        return []

    # 在整个 diff 的新增行里找 test-removal 注解
    added_text = "\n".join(
        l[1:] for l in diff_text.splitlines()
        if l.startswith("+") and not l.startswith("+++")
    )
    m = re.search(
        r'risk:\s*test-removal.*?reason:\s*"([^"]*)".*?owner:.*?reviewed:\s*\d{4}-\d{2}-\d{2}',
        added_text, re.IGNORECASE | re.DOTALL,
    )
    if m:
        reason = m.group(1)
        probs = _validate_annotation_fields(
            "test-removal", reason, _today_iso(), {"test-removal"}, cfg,
        )
        # reviewed 这里不强校验日期 (用今天占位), 只校验 reason
        probs = [p for p in probs if "reviewed" not in p]
        if not probs:
            return []
        return [f"删除了 {len(removed_tests)} 个测试, test-removal 注解无效: {'; '.join(probs)}"]
    return [
        f"删除了 {len(removed_tests)} 个测试声明, 但未找到合法的 risk:test-removal 注解"
    ]


def _today_iso() -> str:
    return dt.date.today().isoformat()


# ============================================================
# 主流程
# ============================================================
def _path_matches(path: str, pattern: str) -> bool:
    """glob 匹配, 支持 ** 跨目录 (fnmatch 原生不支持 **)。"""
    import re as _re
    # 逐段构造正则: ** → 任意(含/); * → 非/; ? → 单字符; 其余转义
    out = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if pattern[i:i+3] == "**/":
            out.append("(?:.*/)?"); i += 3
        elif pattern[i:i+2] == "**":
            out.append(".*"); i += 2
        elif c == "*":
            out.append("[^/]*"); i += 1
        elif c == "?":
            out.append("[^/]"); i += 1
        else:
            out.append(_re.escape(c)); i += 1
    return _re.fullmatch("".join(out), path) is not None


def scan(diff_text: str, cfg: dict) -> list[dict]:
    """返回违规列表。每项: {file, line, type, desc, problems}"""
    violations: list[dict] = []
    parsed = parse_diff(diff_text)
    all_patterns = PATTERNS + build_custom_patterns(cfg)

    # 缓存已读文件
    file_cache: dict[str, list[str] | None] = {}

    exclude = cfg["risk_annotations"].get("scan_exclude_paths", []) or []
    for path, added in parsed.items():
        ext = os.path.splitext(path)[1].lower()
        if ext not in SCAN_EXTENSIONS:
            continue
        # 路径豁免: 生成/引入/第三方代码整文件跳过
        if any(_path_matches(path, pat) for pat in exclude):
            continue
        for lineno, content in added:
            # 收集该行命中的所有风险类型 (一行可能同时命中多个模式)
            hits: list[tuple[str, str]] = []  # (type, desc)
            for rtype, rx, desc in all_patterns:
                if rx.search(content):
                    hits.append((rtype, desc))
            if not hits:
                continue

            hit_types = {t for t, _ in hits}
            descs = "; ".join(d for _, d in hits)

            # 读源文件, 在上方查找注解
            if path not in file_cache:
                file_cache[path] = _read_file_lines(path)
            lines = file_cache[path]
            if lines is None:
                violations.append({
                    "file": path, "line": lineno,
                    "type": "/".join(sorted(hit_types)),
                    "desc": descs,
                    "problems": ["无法读取源文件以校验注解"],
                })
                continue

            # 一条合法注解 (类型属于该行命中类型之一) 即覆盖整行
            ok, problems = find_annotation(lines, lineno, hit_types, cfg)
            if not ok:
                violations.append({
                    "file": path, "line": lineno,
                    "type": "/".join(sorted(hit_types)),
                    "desc": descs, "problems": problems,
                })

    # test-removal 单独检测
    for prob in check_test_removal(diff_text, cfg):
        violations.append({
            "file": "(diff)", "line": 0, "type": "test-removal",
            "desc": "测试删除保护", "problems": [prob],
        })

    return violations


def main() -> int:
    ap = argparse.ArgumentParser(description="MR 治理风险扫描器")
    ap.add_argument("--diff-base", help="diff 基准 ref, 如 origin/master")
    ap.add_argument("--staged", action="store_true", help="扫描已暂存改动")
    ap.add_argument("--config", help="governance.config.yml 路径")
    ap.add_argument("--diff-file", help="从文件读取 diff (测试用)")
    args = ap.parse_args()

    cfg = load_config(args.config)

    if args.diff_file:
        with open(args.diff_file, "r", encoding="utf-8") as f:
            diff_text = f.read()
    else:
        diff_text = get_diff(args.diff_base, args.staged)

    if not diff_text.strip():
        print("[scan-risks] diff 为空, 无需扫描。")
        return 0

    violations = scan(diff_text, cfg)

    if not violations:
        print("[scan-risks] PASS — 未发现缺注解的风险代码。")
        return 0

    print("[scan-risks] FAIL — 以下风险代码缺少合法注解:\n")
    for v in violations:
        loc = f"{v['file']}:{v['line']}" if v["line"] else v["file"]
        # type 可能是 "a/b" 形式 (一行命中多个), 取第一个作为 fix 建议
        first_type = v["type"].split("/")[0]
        print(f"  {loc}")
        print(f"    matched: {v['type']} ({v['desc']})")
        for p in v["problems"]:
            print(f"    problem: {p}")
        print(f"    fix: 在该行上方加  "
              f'// risk:{first_type} reason:"..." owner:@team reviewed:{_today_iso()}')
        print()

    # 区分实质违规(缺注解/字段错/类型未注册/黑名单) 与 纯软提醒([warn] 过期等)
    def _is_blocking(v):
        ps = v.get("problems", [])
        return any(not str(p).lstrip().startswith("[warn]") for p in ps)
    blocking = [v for v in violations if _is_blocking(v)]

    print(f"[scan-risks] 共 {len(violations)} 处违规 "
          f"(其中 {len(blocking)} 处实质问题, {len(violations)-len(blocking)} 处仅提醒)。"
          f"详见 docs/governance/risk-types.md")

    enforcement = cfg["risk_annotations"].get("enforcement", "soft")
    if enforcement != "hard":
        print("[scan-risks] enforcement != hard, 仅警告不阻断。")
        return 0
    if not blocking:
        # 硬模式但只剩软提醒(如继承的过期注解) → 不阻断协作
        print("[scan-risks] 仅软提醒(如注解过期), 不阻断。")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
