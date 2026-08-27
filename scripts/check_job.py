#!/usr/bin/env python3
"""
check_job.py — 检测测试绕过行为（CI 专用）

背景: 直接运行测试命令（如 dotnet test）不经过 record_test_run.py，
CI 看不到测试证据，导致"测了才能合"规则失效。

功能: 扫描 staged diff 中 production 文件的改动，检查是否有对应的测试执行记录。
- 若改动文件有对应测试但无测试记录 -> FAIL
- 若改动文件无测试（如纯 DTO/迁移）-> PASS（通过 check_tested.py 豁免）

此脚本独立运行，不依赖 check_tested.py 的核心逻辑。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import PurePosixPath

try:
    from governance_common import ConfigError, load_config as load_shared_config
except ImportError:
    # 独立运行时的 fallback
    load_shared_config = None
    class ConfigError(Exception): pass

EVIDENCE_PATH = ".governance/test-evidence.jsonl"

DEFAULT_CONFIG = {
    "testing": {
        "enforcement": "hard",
        "exclude_paths": [
            "**/Migrations/**",
            "**/*.Designer.cs",
            "**/*.generated.*",
            "**/Program.cs",
            "**/Startup.cs",
            "**/*Dto.cs",
            "**/*Dtos.cs",
            "**/*.proto",
            "*.sql",
        ],
    },
}

# 生产代码扩展名
PROD_EXTENSIONS = {
    ".cs", ".js", ".ts", ".jsx", ".tsx", ".java", ".go",
    ".py", ".rb", ".php", ".cpp", ".cc", ".c", ".h", ".hpp",
    ".kt", ".rs", ".scala", ".swift", ".dart",
}

# 测试文件判定（与 check_tested.py 保持一致）
_TEST_PATH_RE = re.compile(
    r'(^|/)(tests?|spec|__tests__)/'
    r'|(\.tests?|\.spec|_test|test_)\.[a-z]+$'
    r'|_tests?\.[a-z]+$'
    r'|Tests?\.[a-z]+$',
    re.IGNORECASE,
)


def load_config(path: str | None) -> dict:
    if load_shared_config:
        return load_shared_config(path, DEFAULT_CONFIG, ("testing",))
    return DEFAULT_CONFIG


def run_git(args: list[str]) -> str:
    try:
        return subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout
    except FileNotFoundError:
        sys.stderr.write("[check-job] 找不到 git\n")
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"[check-job] git 失败: {e.stderr}\n")
        sys.exit(2)


def get_staged_files() -> list[str]:
    """获取 staged 状态的文件列表（已暂存待提交的改动）。"""
    out = run_git(["diff", "--cached", "--name-status", "-M", "--no-color"])
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            # 忽略删除(R/D)和重命名(R)，只关心新增(A)和修改(M)
            status = parts[0][0] if parts[0] else ""
            if status in ("A", "M"):
                files.append(parts[-1])
    return files


def is_prod_file(path: str) -> bool:
    """判断是否为生产代码文件（需测试覆盖）。"""
    ext = PurePosixPath(path).suffix.lower()
    return ext in PROD_EXTENSIONS and not _TEST_PATH_RE.search(path)


def is_test_file(path: str) -> bool:
    """判断是否为测试文件。"""
    return bool(_TEST_PATH_RE.search(path))


def find_test_candidates(prod_file: str) -> list[str]:
    """
    根据生产代码文件路径，推测可能的测试文件路径。
    常见命名约定:
    - FooService.cs -> FooServiceTests.cs, FooService.Test.cs, tests/FooServiceTests.cs
    - src/Bar.js -> src/Bar.test.js, src/__tests__/Bar.test.js, test/Bar.test.js
    """
    p = PurePosixPath(prod_file)
    stem = p.stem  # 文件名不含扩展名
    parent = str(p.parent)
    
    candidates = []
    
    # 同目录替换扩展名
    if p.suffix:
        base = str(p.with_suffix(""))
        candidates.extend([
            f"{base}.test{p.suffix}",
            f"{base}.tests{p.suffix}",
            f"{base}_test{p.suffix}",
            f"{base}Test{p.suffix}",
            f"{base}Tests{p.suffix}",
        ])
    
    # test/ 目录下的镜像路径
    for test_dir in ["test", "tests", "__tests__", "spec", "specs"]:
        candidates.extend([
            f"{test_dir}/{stem}.test{p.suffix}",
            f"{test_dir}/{stem}.tests{p.suffix}",
            f"{test_dir}/{stem}_test{p.suffix}",
            f"{test_dir}/{stem}Test{p.suffix}",
            f"{test_dir}/{stem}Tests{p.suffix}",
        ])
        
        # src/Foo.cs -> test/Foo.test.cs（镜像目录结构）
        if parent:
            candidates.append(f"{test_dir}/{p.name}")
    
    return candidates


def test_file_exists(candidates: list[str]) -> bool:
    """检查候选测试文件是否存在。"""
    for candidate in candidates:
        if os.path.isfile(candidate):
            return True
    return False


def load_evidence(path: str) -> list[dict]:
    """加载测试证据文件。"""
    if not os.path.isfile(path):
        return []
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def has_recent_test_record(evidence: list[dict], related_files: list[str]) -> bool:
    """
    检查是否有最近的测试执行记录覆盖了相关文件。
    只看 latest_per_cmd（每条命令的最新一次）。
    """
    latest: dict[str, dict] = {}
    for rec in evidence:
        cmd = rec.get("cmd", "")
        prev = latest.get(cmd)
        if prev is None or str(rec.get("ts", "")) >= str(prev.get("ts", "")):
            latest[cmd] = rec
    
    for rec in latest.values():
        covers = rec.get("covers", []) or []
        # 检查是否覆盖了任何相关文件
        for f in covers:
            if f in related_files:
                return True
        # 检查 cmd 是否与相关文件相关
        cmd = rec.get("cmd", "")
        for f in related_files:
            if f in cmd or PurePosixPath(f).stem in cmd:
                return True
    return False


def _fnmatch_any(path: str, patterns: list[str]) -> bool:
    """fnmatch 支持 ** 前缀。"""
    import fnmatch
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat) or fnmatch.fnmatch(path, pat + "**"):
                return True
        elif fnmatch.fnmatch(path, pat):
            return True
    return False


def check(evidence_path: str = EVIDENCE_PATH) -> tuple[list[str], list[dict]]:
    """
    检查 staged 改动中是否有生产代码绕过测试。
    返回 (errors, violations)
    - errors: 硬阻断错误（如 evidence 显示失败）
    - violations: 缺少测试记录的文件列表
    """
    errors: list[str] = []
    violations: list[dict] = []
    
    # 1. 加载测试证据
    evidence = load_evidence(evidence_path)
    
    # 2. 检查是否有失败的测试记录（无条件硬拦）
    latest: dict[str, dict] = {}
    for rec in evidence:
        cmd = rec.get("cmd", "")
        prev = latest.get(cmd)
        if prev is None or str(rec.get("ts", "")) >= str(prev.get("ts", "")):
            latest[cmd] = rec
    
    for rec in latest.values():
        failed = rec.get("failed")
        if isinstance(failed, int) and failed != 0:
            label = "未知失败(退出码非0)" if failed < 0 else f"{failed} 个用例失败"
            errors.append(f"测试运行记录显示失败: {label} — cmd: {rec.get('cmd', '?')}")
    
    # 3. 获取 staged 改动文件
    staged_files = get_staged_files()
    prod_files = [f for f in staged_files if is_prod_file(f)]
    
    if not prod_files:
        return errors, []
    
    # 4. 配置加载
    cfg = load_config(None)
    exclude = cfg["testing"].get("exclude_paths", [])
    
    for prod_file in prod_files:
        # 豁免路径检查
        if _fnmatch_any(prod_file, exclude):
            continue
        
        # 查找可能的测试文件
        candidates = find_test_candidates(prod_file)
        
        # 如果该生产文件有对应测试文件
        if test_file_exists(candidates):
            # 检查是否有测试执行记录
            related = [c for c in candidates if os.path.isfile(c)]
            if not has_recent_test_record(evidence, related):
                violations.append({
                    "file": prod_file,
                    "test_candidates": related,
                    "reason": "该文件有对应测试，但本次 staged 改动无测试执行记录。"
                })
    
    return errors, violations


def main() -> int:
    parser = argparse.ArgumentParser(description="检测测试绕过行为 (CI 专用)")
    parser.add_argument("--config", help="governance.config.yml 路径")
    parser.add_argument("--evidence", default=EVIDENCE_PATH, help="测试证据文件")
    parser.add_argument("--enforcement", default="hard", choices=["hard", "soft"],
                       help="强制模式")
    args = parser.parse_args()
    
    # 使用局部变量传递给 check()，避免 global 声明问题
    evidence_path = args.evidence
    
    errors, violations = check(evidence_path)
    
    # 硬阻断：失败测试记录
    if errors:
        print("[check-job] FAIL — 存在失败的测试运行记录:\n")
        for e in errors:
            print(f"  ✗ {e}")
        print("\n修复测试后重新运行 record_test_run.py 再提交。")
        return 1
    
    if not violations:
        print("[check-job] PASS — 所有改动的生产代码均有测试执行记录或无对应测试。")
        return 0
    
    print(f"[check-job] FAIL — 以下生产代码有测试但无执行记录:\n")
    for v in violations:
        print(f"  ✗ {v['file']}")
        print(f"    原因: {v['reason']}")
        if v.get('test_candidates'):
            tests = ", ".join(v['test_candidates'][:3])
            print(f"    对应测试: {tests}")
        print()
    
    if args.enforcement == "soft":
        print("[check-job] soft 模式，仅警告不阻断。")
        return 0
    
    print("请使用 record_test_run.py 运行测试以生成测试证据。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
