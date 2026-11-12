from __future__ import annotations

import copy
import datetime as dt
import functools
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


class ConfigError(ValueError):
    pass


def load_config(
    path: str | None,
    defaults: dict[str, Any],
    sections: tuple[str, ...],
) -> dict[str, Any]:
    explicit = path is not None
    if path is None:
        for candidate in ("governance.config.yml", "governance.config.yaml"):
            if os.path.isfile(candidate):
                path = candidate
                break
    if path is None:
        return copy.deepcopy(defaults)
    if not os.path.isfile(path):
        if explicit:
            raise ConfigError(f"配置文件不存在: {path}")
        return copy.deepcopy(defaults)
    if yaml is None:
        raise ConfigError("发现治理配置，但未安装 PyYAML")

    try:
        with open(path, encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
    except Exception as exc:
        raise ConfigError(f"无法解析配置 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置根节点必须是 mapping: {path}")

    merged = copy.deepcopy(defaults)
    merged.update(data)
    for section in sections:
        value = data.get(section, {})
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise ConfigError(f"配置字段 {section} 必须是 mapping")
        # 深度合并：递归合并嵌套字典，避免嵌套键丢失
        merged[section] = _deep_merge(defaults.get(section, {}), value)
    _validate_config(merged)
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    深度合并两个字典。
    - base 中的值会被保留（除非 override 显式覆盖）
    - 若两者都是 dict，递归合并
    - 若 override 中的值为 None，该键会被跳过（保留 base 的值）
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if value is None:
            continue
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate_config(config: dict[str, Any]) -> None:
    for section in ("metadata", "risk_annotations", "testing"):
        value = config.get(section)
        if not isinstance(value, dict):
            continue
        enforcement = value.get("enforcement")
        if enforcement is not None and str(enforcement).lower() not in {"soft", "hard"}:
            raise ConfigError(f"{section}.enforcement 只能是 soft 或 hard")
        deadline = value.get("soft_deadline")
        if deadline:
            try:
                if not isinstance(deadline, dt.date):
                    dt.date.fromisoformat(str(deadline))
            except ValueError as exc:
                raise ConfigError(f"{section}.soft_deadline 不是有效 ISO 日期: {deadline}") from exc


def repository_state() -> str:
    """Hash HEAD plus changed/untracked file content, independent of staging state."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout.strip()
        # quotepath=off + -z: 非 ASCII 文件名不被转义, 否则读不到文件, 内容变化不影响状态指纹
        changed = subprocess.run(
            ["git", "-c", "core.quotepath=off", "diff", "HEAD", "--name-only", "-z", "--no-ext-diff", "--"],
            check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout.split("\0")
        untracked = subprocess.run(
            ["git", "-c", "core.quotepath=off", "ls-files", "-z", "--others", "--exclude-standard"],
            check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout.split("\0")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("无法计算测试证据对应的 Git 状态") from exc

    digest = hashlib.sha256()
    digest.update(head.encode())
    session_prefixes = (".governance/", ".governance\\")
    relevant = {
        name for name in changed + untracked
        if name and not name.startswith(session_prefixes)
    }
    for name in sorted(relevant):
        digest.update(b"\0")
        digest.update(name.replace("\\", "/").encode("utf-8", errors="surrogateescape"))
        path = Path(name)
        if path.is_file():
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        else:
            digest.update(b"\0<deleted>")
    return digest.hexdigest()


@functools.lru_cache(maxsize=1024)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """** → 任意层级 (``**/`` 可为空); * / ? 不跨 ``/``。"""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif pattern.startswith("**", i):
            out.append(".*"); i += 2
        elif pattern[i] == "*":
            out.append("[^/]*"); i += 1
        elif pattern[i] == "?":
            out.append("[^/]"); i += 1
        else:
            out.append(re.escape(pattern[i])); i += 1
    return re.compile("".join(out))


def path_matches(path: str, pattern: str) -> bool:
    """全部治理脚本共用的路径 glob 语义 (gitignore 风格, 大小写敏感, 与 Linux CI 一致):

    - 以 ``/`` 结尾视为目录前缀: ``ci/`` 等价 ``ci/**``
    - 不含 ``/`` 的模式匹配任意一级路径段: ``*.md`` 命中 ``docs/a.md``
    - 含 ``/`` 的模式从仓库根锚定: ``**/auth/**`` 也命中根目录 ``auth/x.py``
    """
    path = path.replace("\\", "/")
    if pattern.endswith("/"):
        pattern += "**"
    regex = _glob_regex(pattern)
    if "/" not in pattern:
        return any(regex.fullmatch(part) for part in path.split("/"))
    return regex.fullmatch(path) is not None


def path_matches_any(path: str, patterns: list[str]) -> bool:
    return any(path_matches(path, pattern) for pattern in patterns)


def reason_blacklist_hit(reason: str, blacklist: list[str]) -> str | None:
    """返回 reason 命中的第一个黑名单词。

    ASCII 词按词边界匹配 (``temp`` 不误伤 ``template``, ``wip`` 不误伤 ``wipe``);
    中文词没有词边界, 仍按子串匹配。忽略大小写。
    """
    for bad in blacklist:
        word = str(bad)
        if word.isascii():
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", reason, re.IGNORECASE):
                return word
        elif word.lower() in reason.lower():
            return word
    return None
