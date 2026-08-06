"""Small, dependency-free helpers for AgentGate language profiles."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

DEFAULT_SOURCE_EXTENSIONS = frozenset({
    ".c", ".cc", ".cpp", ".cs", ".dart", ".go", ".h", ".hpp", ".java",
    ".js", ".jsx", ".json", ".kt", ".php", ".py", ".rb", ".rs", ".scala",
    ".sh", ".sql", ".swift", ".ts", ".tsx", ".xml", ".yaml", ".yml",
})


def load_profile(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    profile_path = Path(path)
    raw = profile_path.read_text(encoding="utf-8-sig")
    data = yaml.safe_load(raw) if yaml is not None else json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"profile must be a mapping: {profile_path}")
    return data


def source_extensions(profile: dict[str, Any] | None = None) -> frozenset[str]:
    profile = profile or {}
    values = profile.get("source_extensions")
    if not isinstance(values, list) and isinstance(profile.get("languages"), dict):
        values = profile["languages"].get("source_extensions")
    if not isinstance(values, list):
        return DEFAULT_SOURCE_EXTENSIONS
    return frozenset(str(value).lower() for value in values)


def test_patterns(profile: dict[str, Any] | None = None) -> tuple[str, ...]:
    values = (profile or {}).get("test_patterns")
    return tuple(str(value) for value in values) if isinstance(values, list) else ()
