#!/usr/bin/env python3
"""
validate_yaml.py — 批量验证项目中的 YAML 配置文件语法

用法:
    python3 validate_yaml.py                    # 检查默认路径
    python3 validate_yaml.py path1/ path2/      # 检查指定路径
    python3 validate_yaml.py --ci               # CI 模式: 检查所有治理配置
"""
import sys
import os
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("[validate-yaml] 缺少 pyyaml, 安装: pip install pyyaml\n")
    sys.exit(1)

DEFAULT_PATHS = [
    "governance.config.yml",
    "governance/ci-snippet.yml",
    "governance/patterns/",
    "governance/profiles/",
    ".gitlab-ci.yml",
]

def find_yaml_files(paths):
    """递归查找所有 YAML 文件"""
    files = []
    for p in paths:
        path = Path(p)
        if path.is_file():
            if path.suffix in (".yml", ".yaml"):
                files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("*.yml"))
            files.extend(path.rglob("*.yaml"))
    return sorted(set(files))

def validate_file(path):
    """验证单个 YAML 文件，返回 (ok, error_msg)"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            yaml.safe_load(f)
        return (True, None)
    except yaml.YAMLError as e:
        return (False, str(e))
    except Exception as e:
        return (False, f"读取失败: {e}")

def main():
    ci_mode = "--ci" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    
    # 确定扫描路径
    if args:
        paths = args
    else:
        paths = DEFAULT_PATHS
    
    # 只检查存在的路径
    existing = [p for p in paths if Path(p).exists()]
    if not existing:
        sys.stderr.write(f"[validate-yaml] 未找到任何 YAML 文件在: {', '.join(paths)}\n")
        sys.exit(0 if ci_mode else 1)
    
    files = find_yaml_files(existing)
    if not files:
        sys.stderr.write("[validate-yaml] 未找到任何 .yml/.yaml 文件\n")
        sys.exit(0 if ci_mode else 1)
    
    print(f"[validate-yaml] 检查 {len(files)} 个 YAML 文件...")
    
    errors = []
    for f in files:
        ok, err = validate_file(f)
        if ok:
            print(f"  ✓ {f}")
        else:
            print(f"  ✗ {f}")
            print(f"    {err}")
            errors.append((f, err))
    
    if errors:
        print(f"\n[validate-yaml] FAIL — {len(errors)} 个文件语法错误")
        sys.exit(1)
    else:
        print(f"\n[validate-yaml] PASS — 所有 YAML 文件语法正确")
        sys.exit(0)

if __name__ == "__main__":
    main()
