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

from governance_common import ConfigError, load_config as load_shared_config

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
            # 扫描器自身不扫自己 (脚本里含风险模式的字面示例, 会自指误报)
            "**/governance/scripts/**", "governance/scripts/**",
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

# 测试文件判定 (与 check_tested.py 保持一致)
_TEST_FILE_RE = re.compile(
    r'(^|/)(tests?|spec|__tests?__)(/|$)'
    r'|(_test|_spec)\.[a-z]+$'
    r'|(^|/)test_[^/]+$',
    re.IGNORECASE,
)

# 测试文件中仍然需要检查的模式 (skipped-test 的检测对象就是测试代码本身)
_TEST_FILE_PATTERNS = {"skipped-test"}


def _is_test_file(path: str) -> bool:
    return bool(_TEST_FILE_RE.search(path))


# ============================================================
# 风险模式定义
# 每个模式: (类型名, 编译后的正则, 说明, 适用扩展名集合, 模式)
#   适用扩展名: frozenset, 空集合 = 所有语言
#   模式: "block" (硬阻断) | "warn" (仅提醒, 不阻断)
# 这些正则有意保守 —— 宁可少报也尽量减少误报, 真正的精确度靠 reason 注解兜底
# ============================================================
def _build_patterns() -> list[tuple[str, re.Pattern, str, frozenset, str]]:
    p: list[tuple[str, re.Pattern, str, frozenset, str]] = []

    # 1. auth-bypass: 通用 — 认证字段与字面量字符串比较
    p.append((
        "auth-bypass",
        re.compile(
            r'(?i)\b(\w*(?:user|admin|role|account|uid|owner|tenant|auth)\w*)\b'
            r'\s*(?:==|!=|\.Equals\s*\()\s*"[^"]+"'
        ),
        "认证字段与字面量字符串比较",
        frozenset(),   # 所有语言
        "block",
    ))

    # 2. magic-id: 通用 — 硬编码 ObjectId / UUID / 长数字
    p.append((
        "magic-id",
        re.compile(
            r'"(?:[0-9a-fA-F]{24}'                              # mongo ObjectId
            r'|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'   # uuid
            r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
            r'|\d{12,})"'                                        # 长数字串
        ),
        "硬编码 ObjectId / UUID / 长数字 ID",
        frozenset(),
        "block",
    ))

    # 3. swallowed-exception: 空 catch 块 — 仅适用于有 catch 语法的语言 (非 Go/Python/Ruby/Rust)
    p.append((
        "swallowed-exception",
        re.compile(r'\bcatch\b[^{]*\{\s*\}'),
        "catch 块为空 (吞异常)",
        frozenset({".cs", ".java", ".js", ".ts", ".jsx", ".tsx",
                   ".cpp", ".cc", ".c", ".h", ".hpp", ".kt", ".scala", ".swift"}),
        "block",
    ))

    # 4. suppressed-warning: 通用 — nolint(Go), noqa(Python), eslint-disable(JS) 等
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
        frozenset(),
        "block",
    ))

    # 5. skipped-test: 跳过/忽略测试 — Go 无 [Fact]/pytest 语法, 不适用
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
        frozenset({".cs", ".py", ".js", ".ts", ".jsx", ".tsx",
                   ".java", ".kt", ".rb", ".scala"}),
        "block",
    ))

    # 6. time-bypass: 通用 — time.Now() 是 Go, DateTime.Now 是 C#, Date.now() 是 JS
    p.append((
        "time-bypass",
        re.compile(
            r'(?i)(DateTime\.(?:Now|UtcNow|Today)|time\.Now\(\)|Date\.now\(\))'
            r'.{0,40}?(?:[<>]=?|==)'
            r'.{0,20}?(?:new\s+DateTime\s*\(\s*\d{4}|"\d{4}-\d{2}-\d{2}")'
        ),
        "当前时间与字面量日期比较",
        frozenset(),
        "block",
    ))

    # 7. env-hardcode: ASPNETCORE_ENVIRONMENT/NODE_ENV 不适用于 Go
    p.append((
        "env-hardcode",
        re.compile(
            r'(?i)(?:env|environment|aspnetcore_environment|node_env)'
            r'\w*\s*(?:==|!=|\.Equals\s*\()\s*"'
            r'(?:prod|production|dev|development|staging|test|local)"'
        ),
        "环境字符串硬编码判断行为",
        frozenset({".cs", ".js", ".ts", ".jsx", ".tsx", ".py",
                   ".java", ".kt", ".rb", ".php"}),
        "block",
    ))

    # 8. todo-no-context: 通用
    p.append((
        "todo-no-context",
        re.compile(r'(?i)\b(TODO|FIXME|HACK)(?![-\w])(?!\s*\(\s*@?\w+\s*,\s*\d{4}-\d{2}-\d{2})'),
        "TODO/FIXME/HACK 缺少 (owner, 日期)",
        frozenset(),
        "block",
    ))

    return p


PATTERNS = _build_patterns()


def build_custom_patterns(cfg: dict) -> list:
    """读取 config 的 risk_annotations.custom_patterns, 编译公司自定义规则。
    每条: {type, regex, desc, exts?, mode?}。正则无效则跳过并警告, 不中断扫描。
    返回 5-tuple: (type, compiled_regex, desc, exts_frozenset, mode)。"""
    out = []
    ra = cfg.get("risk_annotations", {}) if isinstance(cfg, dict) else {}
    for item in (ra.get("custom_patterns") or []):
        t = (item or {}).get("type")
        rx = (item or {}).get("regex")
        desc = (item or {}).get("desc") or t
        exts_raw = (item or {}).get("exts") or []
        mode = str((item or {}).get("mode") or "block").lower()
        if mode not in ("block", "warn"):
            mode = "block"
        if not t or not rx:
            continue
        try:
            out.append((t, re.compile(rx), desc, frozenset(exts_raw), mode))
        except re.error as e:
            message = f"custom_pattern {t} regex invalid: {e}"
            if str(ra.get("enforcement", "soft")).lower() == "hard":
                raise ConfigError(message)
            sys.stderr.write(f"[scan-risks] {message}, skipped\n")
    return out


# ============================================================
# 配置加载
# ============================================================
def _load_pattern_includes(cfg: dict, config_path: str | None) -> None:
    """加载 risk_annotations.pattern_includes 列出的外部规则文件, 合并进 custom_patterns。
    路径相对于 config_path 所在目录。新规则的 type 自动加入 registered_types。"""
    if not _HAS_YAML:
        return
    includes = cfg["risk_annotations"].get("pattern_includes") or []
    if not includes:
        return
    base_dir = (
        os.path.dirname(os.path.abspath(config_path))
        if config_path else os.getcwd()
    )
    existing = list(cfg["risk_annotations"].get("custom_patterns") or [])
    registered = set(cfg["risk_annotations"].get("registered_types") or [])
    for inc in includes:
        full_path = inc if os.path.isabs(inc) else os.path.join(base_dir, inc)
        if not os.path.isfile(full_path):
            sys.stderr.write(f"[scan-risks] pattern_includes: 文件不存在: {full_path}\n")
            continue
        try:
            import yaml as _yaml  # type: ignore
            with open(full_path, encoding="utf-8") as f:
                data = _yaml.safe_load(f) or {}
        except Exception as e:
            sys.stderr.write(f"[scan-risks] pattern_includes: 无法读取 {full_path}: {e}\n")
            continue
        for pat in (data.get("patterns") or []):
            existing.append(pat)
            if pat.get("type"):
                registered.add(pat["type"])
    cfg["risk_annotations"]["custom_patterns"] = existing
    cfg["risk_annotations"]["registered_types"] = list(registered)


def load_config(path: str | None) -> dict:
    cfg = load_shared_config(path, DEFAULT_CONFIG, ("risk_annotations",))
    # 解析 pattern_includes 时需要 config 文件的实际路径
    resolved = path
    if resolved is None:
        for candidate in ("governance.config.yml", "governance.config.yaml"):
            if os.path.isfile(candidate):
                resolved = candidate
                break
    _load_pattern_includes(cfg, resolved)
    return cfg


# ============================================================
# git 交互
# ============================================================
def run_git(args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
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

    # 每个命中类型都必须由一条对应注解覆盖。
    matches = list(_RISK_INLINE_RE.finditer(window_text))
    if matches:
        covered: set[str] = set()
        problems: list[str] = []
        for m in matches:
            risk_type = m.group("type").lower()
            current = _validate_annotation_fields(
                risk_type, m.group("reason"), m.group("reviewed"), expected_types, cfg
            )
            if not current or all(p.startswith("[warn]") for p in current):
                covered.add(risk_type)
            else:
                problems.extend(current)
        missing = expected_types - covered
        if missing:
            problems.append(f'缺少风险类型注解: {", ".join(sorted(missing))}')
        return (not problems, problems)

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
    r'|\bfunc\s+Test\w+'          # Go
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
    matches = list(re.finditer(
        r'risk:\s*test-removal.*?reason:\s*"([^"]*)".*?owner:.*?reviewed:\s*(\d{4}-\d{2}-\d{2})',
        added_text, re.IGNORECASE | re.DOTALL,
    ))
    valid = 0
    invalid: list[str] = []
    for m in matches:
        probs = _validate_annotation_fields(
            "test-removal", m.group(1), m.group(2), {"test-removal"}, cfg,
        )
        if probs:
            invalid.extend(probs)
        else:
            valid += 1
    if valid >= len(removed_tests):
        return []
    detail = f"，另有无效注解: {'; '.join(invalid)}" if invalid else ""
    return [
        f"删除了 {len(removed_tests)} 个测试，但只有 {valid} 条合法 test-removal 注解；"
        f"每个被删除测试都必须单独说明{detail}"
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
    """返回违规列表。每项: {file, line, type, desc, problems, mode}"""
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
        is_test = _is_test_file(path)
        added_by_line = dict(added)
        for lineno, content in added:
            window = [content]
            for offset in range(1, 5):
                following = added_by_line.get(lineno + offset)
                if following is None:
                    break
                window.append(following)
            scan_text = "\n".join(window)
            # 收集该行命中的所有风险类型 (一行可能同时命中多个模式)
            hits: list[tuple[str, str, str]] = []  # (type, desc, mode)
            for rtype, rx, desc, exts, pmode in all_patterns:
                # 扩展名过滤: 模式限定了语言且当前文件不在其中则跳过
                if exts and ext not in exts:
                    continue
                # 测试文件: 只检查测试代码本身相关的模式 (如 skipped-test)
                if is_test and rtype not in _TEST_FILE_PATTERNS:
                    continue
                match = rx.search(scan_text)
                if match and match.start() <= len(content):
                    hits.append((rtype, desc, pmode))
            if not hits:
                continue

            hit_types = {t for t, _, _ in hits}
            descs = "; ".join(d for _, d, _ in hits)
            # 所有命中模式均为 warn 时, 该违规为仅警告; 有任一 block 模式则阻断
            violation_mode = "warn" if all(m == "warn" for _, _, m in hits) else "block"

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
                    "mode": violation_mode,
                })
                continue

            # 一条合法注解 (类型属于该行命中类型之一) 即覆盖整行
            ok, problems = find_annotation(lines, lineno, hit_types, cfg)
            if not ok:
                violations.append({
                    "file": path, "line": lineno,
                    "type": "/".join(sorted(hit_types)),
                    "desc": descs, "problems": problems,
                    "mode": violation_mode,
                })

    # test-removal 单独检测
    for prob in check_test_removal(diff_text, cfg):
        violations.append({
            "file": "(diff)", "line": 0, "type": "test-removal",
            "desc": "测试删除保护", "problems": [prob],
            "mode": "block",
        })

    return violations


def main() -> int:
    ap = argparse.ArgumentParser(description="MR 治理风险扫描器")
    ap.add_argument("--diff-base", help="diff 基准 ref, 如 origin/master")
    ap.add_argument("--staged", action="store_true", help="扫描已暂存改动")
    ap.add_argument("--config", help="governance.config.yml 路径")
    ap.add_argument("--diff-file", help="从文件读取 diff (测试用)")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        sys.stderr.write(f"[scan-risks] 配置错误: {exc}\n")
        return 2

    if args.diff_file:
        with open(args.diff_file, "r", encoding="utf-8") as f:
            diff_text = f.read()
    else:
        diff_text = get_diff(args.diff_base, args.staged)

    if not diff_text.strip():
        print("[scan-risks] diff 为空, 无需扫描。")
        return 0

    try:
        violations = scan(diff_text, cfg)
    except ConfigError as exc:
        sys.stderr.write(f"[scan-risks] 配置错误: {exc}\n")
        return 2

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

    # 区分实质违规 与 纯软提醒
    # warn 模式的违规 (来自 mode:warn 规则) 不阻断; [warn] 前缀问题同样不阻断
    def _is_blocking(v):
        if v.get("mode") == "warn":
            return False
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
