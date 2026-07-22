#!/usr/bin/env python3
"""
run_affected_tests.py — 对本次 diff 受影响的 Go package 真跑 go test

原理:
  1. 从 git diff 里提取变更的 .go 文件
  2. 推导出它们所属的 Go package 路径 (文件所在目录)
  3. 对每个受影响的 package 执行 go test ./pkg/...
  4. 以 go test 的真实退出码作为门禁结果

设计约束:
  - 只适合标准 go test 环境; Bazel 单仓需另行配置 (见 CI 注释)
  - 无本地修改或无 .go 文件变更时直接通过 (exit 0)
  - CI job 退出码即门禁, 不写本地证据文件 (证据只在本地 hook 有意义)

用法:
    python run_affected_tests.py --diff-base origin/master
    python run_affected_tests.py --staged
    python run_affected_tests.py --diff-base HEAD~1 --timeout 300

退出码:
    0  无受影响 Go 包, 或全部测试通过
    1  测试失败
    2  运行错误 (git/go 不可用等)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

_FILE_RE = re.compile(r'^\+\+\+ (?:b/)?(.+)$')


def run_git(args: list[str]) -> str:
    try:
        return subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout
    except FileNotFoundError:
        sys.stderr.write("[go-test] 错误: 找不到 git\n")
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"[go-test] git 失败: {e.stderr}\n")
        sys.exit(2)


def get_diff(diff_base: str | None, staged: bool) -> str:
    if staged:
        return run_git(["diff", "--cached", "--name-only", "--no-color"])
    if diff_base:
        return run_git(["diff", f"{diff_base}...HEAD", "--name-only", "--no-color"])
    return run_git(["diff", "HEAD~1...HEAD", "--name-only", "--no-color"])


def affected_packages(diff_output: str) -> list[str]:
    """从 git diff --name-only 输出推导受影响的 Go package 目录。"""
    dirs: set[str] = set()
    for line in diff_output.splitlines():
        path = line.strip()
        if not path.endswith(".go"):
            continue
        # 跳过测试文件本身不产生新 package (测试文件属于被测 package)
        pkg_dir = os.path.dirname(path) or "."
        dirs.add(pkg_dir)
    return sorted(dirs)


def find_go_module_root() -> str | None:
    """向上查找 go.mod, 返回其所在目录; 找不到返回 None。"""
    cur = os.path.abspath(".")
    while True:
        if os.path.isfile(os.path.join(cur, "go.mod")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def run_tests(packages: list[str], timeout: int) -> int:
    """对每个 package 运行 go test, 返回整体退出码 (0=全绿, 1=有失败)。"""
    if not packages:
        print("[go-test] 无受影响的 Go 包, 跳过。")
        return 0

    module_root = find_go_module_root()
    if module_root is None:
        sys.stderr.write("[go-test] 未找到 go.mod, 无法运行 go test。\n")
        return 2

    print(f"[go-test] 受影响包 ({len(packages)} 个): {', '.join(packages)}")

    overall = 0
    for pkg in packages:
        # 构造 ./pkg/... 形式的测试路径
        test_path = f"./{pkg}/..." if pkg != "." else "./..."
        cmd = ["go", "test", f"-timeout={timeout}s", "-count=1", test_path]
        print(f"[go-test] 运行: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                cwd=module_root,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                overall = 1
        except FileNotFoundError:
            sys.stderr.write("[go-test] 错误: 找不到 go 命令, 请确认 Go 已安装。\n")
            return 2

    if overall == 0:
        print("[go-test] PASS — 所有受影响包测试通过。")
    else:
        print("[go-test] FAIL — 存在测试失败, 请修复后重试。")
    return overall


def main() -> int:
    ap = argparse.ArgumentParser(description="对受影响 Go 包运行 go test")
    ap.add_argument("--diff-base", help="diff 基准 ref, 如 origin/master")
    ap.add_argument("--staged", action="store_true", help="检查已暂存改动")
    ap.add_argument("--timeout", type=int, default=120,
                    help="单次 go test 超时秒数 (默认 120)")
    args = ap.parse_args()

    diff_output = get_diff(args.diff_base, args.staged)
    if not diff_output.strip():
        print("[go-test] diff 为空, 无需测试。")
        return 0

    packages = affected_packages(diff_output)
    return run_tests(packages, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
