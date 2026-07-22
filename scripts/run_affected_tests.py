#!/usr/bin/env python3
"""
run_affected_tests.py — 对本次 diff 受影响的 Go package 真跑 go test

原理:
  1. 从 git diff 里提取变更的 .go 文件
  2. 推导出它们所属的 Go package 路径 (文件所在目录)
  3. 用 go list 建反向依赖图, 找出 import 这些 package 的 importers
  4. 合并直接改动包 + importers, 对所有受影响包执行 go test
  5. 以 go test 的真实退出码作为门禁结果

设计约束:
  - 只适合标准 go test 环境; Bazel 单仓需另行配置 (见 CI 注释)
  - 无本地修改或无 .go 文件变更时直接通过 (exit 0)
  - CI job 退出码即门禁, 不写本地证据文件 (证据只在本地 hook 有意义)
  - go list 失败时退化为只测直接改动包 (宽容降级, 不阻断 CI)

用法:
    python run_affected_tests.py --diff-base origin/master
    python run_affected_tests.py --staged
    python run_affected_tests.py --diff-base HEAD~1 --timeout 300
    python run_affected_tests.py --diff-base origin/main --no-reverse-deps

退出码:
    0  无受影响 Go 包, 或全部测试通过
    1  测试失败
    2  运行错误 (git/go 不可用等)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


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
    """从 git diff --name-only 输出推导直接受影响的 Go package 目录。"""
    dirs: set[str] = set()
    for line in diff_output.splitlines():
        path = line.strip()
        if not path.endswith(".go"):
            continue
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


def get_module_name(module_root: str) -> str | None:
    """从 go.mod 读取 module 名称。"""
    go_mod = os.path.join(module_root, "go.mod")
    try:
        with open(go_mod, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("module "):
                    return line.split()[1]
    except OSError:
        pass
    return None


def build_reverse_dep_map(module_root: str) -> dict[str, set[str]]:
    """用 go list -json ./... 构建 import 反向依赖图。
    返回: {被依赖的 import path → {依赖它的 import path, ...}}
    失败时返回空 dict (宽容降级)。"""
    try:
        out = subprocess.run(
            ["go", "list", "-json", "./..."],
            cwd=module_root,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if out.returncode != 0:
        return {}

    # go list -json 输出多个拼接的 JSON 对象, 逐个解析
    reverse: dict[str, set[str]] = {}
    buf = ""
    depth = 0
    for line in out.stdout.splitlines():
        buf += line + "\n"
        depth += line.count("{") - line.count("}")
        if depth == 0 and buf.strip():
            try:
                pkg = json.loads(buf)
                importer = pkg.get("ImportPath", "")
                for dep in pkg.get("Imports", []):
                    reverse.setdefault(dep, set()).add(importer)
            except json.JSONDecodeError:
                pass
            buf = ""
    return reverse


def expand_with_importers(
    direct_dirs: list[str],
    module_root: str,
    reverse_map: dict[str, set[str]],
) -> list[str]:
    """把直接改动的目录转换成 import path, 再查反向依赖, 返回合并后的目录列表。"""
    if not reverse_map:
        return direct_dirs

    module_name = get_module_name(module_root)
    if not module_name:
        return direct_dirs

    # 目录 → import path
    expanded: set[str] = set()
    new_dirs: set[str] = set(direct_dirs)

    for pkg_dir in direct_dirs:
        # 推导 import path: module_name + "/" + 相对于 module_root 的路径
        abs_dir = os.path.abspath(pkg_dir)
        try:
            rel = os.path.relpath(abs_dir, module_root).replace("\\", "/")
        except ValueError:
            continue
        import_path = module_name if rel == "." else f"{module_name}/{rel}"
        expanded.add(import_path)

        # 递归查所有 importers (BFS 一层, 不递归传播, 避免全量重测)
        for importer in reverse_map.get(import_path, set()):
            # import path → 目录: 去掉 module_name 前缀
            if importer.startswith(module_name + "/"):
                sub = importer[len(module_name) + 1:]
                new_dirs.add(sub)
            elif importer == module_name:
                new_dirs.add(".")

    added = sorted(new_dirs - set(direct_dirs))
    if added:
        print(f"[go-test] 反向依赖扩展: +{len(added)} 个 importer 包 ({', '.join(added[:5])}"
              f"{'...' if len(added) > 5 else ''})")
    return sorted(new_dirs)


def run_tests(packages: list[str], timeout: int) -> int:
    """对每个 package 运行 go test, 返回整体退出码。"""
    if not packages:
        print("[go-test] 无受影响的 Go 包, 跳过。")
        return 0

    module_root = find_go_module_root()
    if module_root is None:
        sys.stderr.write("[go-test] 未找到 go.mod, 无法运行 go test。\n")
        return 2

    print(f"[go-test] 测试包 ({len(packages)} 个): {', '.join(packages)}")

    overall = 0
    for pkg in packages:
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
    ap = argparse.ArgumentParser(description="对受影响 Go 包（含反向依赖）运行 go test")
    ap.add_argument("--diff-base", help="diff 基准 ref, 如 origin/master")
    ap.add_argument("--staged", action="store_true", help="检查已暂存改动")
    ap.add_argument("--timeout", type=int, default=120,
                    help="单次 go test 超时秒数 (默认 120)")
    ap.add_argument("--no-reverse-deps", action="store_true",
                    help="跳过反向依赖展开, 只测直接改动的包")
    args = ap.parse_args()

    diff_output = get_diff(args.diff_base, args.staged)
    if not diff_output.strip():
        print("[go-test] diff 为空, 无需测试。")
        return 0

    direct = affected_packages(diff_output)
    if not direct:
        print("[go-test] 无 .go 文件变更, 跳过。")
        return 0

    if args.no_reverse_deps:
        packages = direct
    else:
        module_root = find_go_module_root()
        if module_root:
            reverse_map = build_reverse_dep_map(module_root)
            packages = expand_with_importers(direct, module_root, reverse_map)
        else:
            packages = direct

    return run_tests(packages, args.timeout)


if __name__ == "__main__":
    sys.exit(main())

