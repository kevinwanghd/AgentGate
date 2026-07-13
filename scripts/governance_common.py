from __future__ import annotations

import copy
import datetime as dt
import hashlib
import os
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
        merged[section] = {**defaults.get(section, {}), **value}
    _validate_config(merged)
    return merged


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
        changed = subprocess.run(
            ["git", "diff", "HEAD", "--name-only", "--no-ext-diff", "--"],
            check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout.splitlines()
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout.splitlines()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("无法计算测试证据对应的 Git 状态") from exc

    digest = hashlib.sha256()
    digest.update(head.encode())
    session_prefixes = (".governance/", ".governance\\")
    relevant = {
        name for name in changed + untracked
        if not name.startswith(session_prefixes)
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
