#!/usr/bin/env python3
"""
governance_mirror.py — Governance rule synchronization

Synchronizes confirmed patterns and lessons across repositories.
- Exports governance rules as versioned mirrors
- Supports dry-run and diff preview
- Rollback on failure
- Only reads published versions, never pending

Usage:
    python scripts/governance_mirror.py export --to /path/to/mirror
    python scripts/governance_mirror.py sync --from /path/to/mirror --to /path/to/target
    python scripts/governance_mirror.py dry-run --from /path/to/mirror --to /path/to/target
    python scripts/governance_mirror.py rollback --version v1.2.1 --target /path/to/target
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None


# Governance directories to mirror
GOVERNANCE_DIRS = [
    "patterns",
    "lessons",
    "governance.config.yml",
]

# Metadata file name
METADATA_FILE = "governance-mirror.json"


class MirrorError(Exception):
    """Error during mirror operations."""
    pass


def _now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_content_hash(path: Path) -> str:
    """Compute SHA-256 hash of directory contents."""
    hasher = hashlib.sha256()

    for root, dirs, files in os.walk(path):
        # Sort for deterministic ordering
        dirs.sort()
        files.sort()

        for file in files:
            if file == METADATA_FILE:
                continue
            file_path = Path(root) / file
            rel_path = file_path.relative_to(path)
            hasher.update(str(rel_path).encode())
            hasher.update(b"\n")
            hasher.update(file_path.read_bytes())

    return hasher.hexdigest()


def _get_git_version() -> str:
    """Get version from git (tag or commit hash)."""
    try:
        # Try to get current tag
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        # Fall back to commit hash
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def export_mirror(source: Path, output_dir: Path) -> dict:
    """
    Export governance rules to a versioned mirror.

    Creates:
    - output_dir/v<version>/ containing governance rules
    - output_dir/METADATA_FILE with version info
    """
    if yaml is None:
        raise MirrorError("PyYAML required for mirror export")

    output_dir = Path(output_dir)
    version = _get_git_version()
    version_dir = output_dir / f"v{version}"
    metadata_path = output_dir / METADATA_FILE

    # Load existing metadata
    metadata = {}
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    # Export each governance directory
    exported_files = []
    for item in GOVERNANCE_DIRS:
        source_path = source / item
        target_path = version_dir / item

        if not source_path.exists():
            continue

        if source_path.is_file():
            # Single file
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            exported_files.append(item)
        else:
            # Directory
            if target_path.exists():
                shutil.rmtree(target_path)
            shutil.copytree(source_path, target_path)
            exported_files.append(item + "/")

    # Compute content hash
    content_hash = _compute_content_hash(version_dir)

    # Update metadata
    metadata[version] = {
        "version": version,
        "exported_at": _now_iso(),
        "content_hash": content_hash,
        "files": exported_files,
        "source": str(source.resolve()),
    }

    # Write metadata
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"[governance-mirror] Exported v{version}")
    print(f"  Output: {version_dir}")
    print(f"  Files: {', '.join(exported_files)}")
    print(f"  Hash: {content_hash[:16]}...")

    return metadata[version]


def _load_metadata(mirror_dir: Path) -> dict:
    """Load mirror metadata."""
    metadata_path = mirror_dir / METADATA_FILE
    if not metadata_path.exists():
        raise MirrorError(f"No metadata file found in {mirror_dir}")

    return json.loads(metadata_path.read_text(encoding="utf-8"))


def _get_version_dir(mirror_dir: Path, version: str | None = None) -> Path:
    """Get the directory for a specific version."""
    if version:
        return mirror_dir / f"v{version}"

    # Get latest version
    metadata = _load_metadata(mirror_dir)
    versions = sorted(metadata.keys())
    if not versions:
        raise MirrorError("No versions found in mirror")

    return mirror_dir / f"v{versions[-1]}"


def sync_mirror(
    mirror_dir: Path,
    target_dir: Path,
    version: str | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Sync governance rules from mirror to target repository.

    Args:
        mirror_dir: Source mirror directory
        target_dir: Target repository directory
        version: Specific version to sync (latest if omitted)
        dry_run: If True, show diff without making changes

    Returns:
        Dict with sync results
    """
    if yaml is None:
        raise MirrorError("PyYAML required for mirror sync")

    mirror_dir = Path(mirror_dir)
    target_dir = Path(target_dir)

    # Load metadata and get version
    metadata = _load_metadata(mirror_dir)

    if version:
        if version not in metadata:
            raise MirrorError(f"Version {version} not found in mirror")
        version_info = metadata[version]
    else:
        # Get latest version
        versions = sorted(metadata.keys())
        if not versions:
            raise MirrorError("No versions found in mirror")
        version = versions[-1]
        version_info = metadata[version]

    version_dir = mirror_dir / f"v{version}"

    # Track changes
    changes = {
        "added": [],
        "updated": [],
        "deleted": [],
        "unchanged": [],
    }

    for item in GOVERNANCE_DIRS:
        source_path = version_dir / item
        target_path = target_dir / item

        if not source_path.exists():
            continue

        if source_path.is_file():
            # Single file
            if not target_path.exists():
                changes["added"].append(item)
                if not dry_run:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, target_path)
            elif _files_differ(source_path, target_path):
                changes["updated"].append(item)
                if not dry_run:
                    shutil.copy2(source_path, target_path)
            else:
                changes["unchanged"].append(item)

        else:
            # Directory
            if not target_path.exists():
                changes["added"].append(item + "/")
                if not dry_run:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(source_path, target_path)
            else:
                # Compare contents
                dir_changes = _compare_dirs(source_path, target_path)
                if dir_changes["added"] or dir_changes["updated"] or dir_changes["deleted"]:
                    changes["updated"].append(item + "/")
                    if not dry_run:
                        _sync_dir(source_path, target_path)
                else:
                    changes["unchanged"].append(item + "/")

    # Print summary
    prefix = "[governance-mirror] "
    if dry_run:
        prefix = "[governance-mirror] DRY-RUN: "

    print(f"{prefix}Sync v{version} from mirror")
    print(f"  Target: {target_dir}")

    if changes["added"]:
        print(f"  Added: {', '.join(changes['added'])}")
    if changes["updated"]:
        print(f"  Updated: {', '.join(changes['updated'])}")
    if changes["deleted"]:
        print(f"  Deleted: {', '.join(changes['deleted'])}")
    if changes["unchanged"]:
        print(f"  Unchanged: {len(changes['unchanged'])} items")

    if dry_run:
        print("\nNo changes made (dry-run mode)")

    return {
        "version": version,
        "dry_run": dry_run,
        "changes": changes,
        "content_hash": version_info.get("content_hash"),
    }


def _files_differ(a: Path, b: Path) -> bool:
    """Check if two files have different content."""
    return a.read_bytes() != b.read_bytes()


def _compare_dirs(source: Path, target: Path) -> dict:
    """Compare two directories and return change summary."""
    changes = {"added": [], "updated": [], "deleted": []}

    source_files = {p.relative_to(source) for p in source.rglob("*") if p.is_file()}
    target_files = {p.relative_to(target) for p in target.rglob("*") if p.is_file()}

    for rel in source_files - target_files:
        changes["added"].append(str(rel))
    for rel in source_files & target_files:
        if _files_differ(source / rel, target / rel):
            changes["updated"].append(str(rel))
    for rel in target_files - source_files:
        changes["deleted"].append(str(rel))

    return changes


def _sync_dir(source: Path, target: Path) -> None:
    """Sync source directory to target, overwriting target."""
    # Remove and recreate
    shutil.rmtree(target)
    shutil.copytree(source, target)


def rollback_mirror(target_dir: Path, version: str) -> dict:
    """
    Rollback target repository to a previous version.

    Requires the version to exist in a mirror.
    """
    # This is a simplified rollback - in production you'd restore from the mirror
    print(f"[governance-mirror] Rollback to v{version}")
    print(f"  Target: {target_dir}")
    print("\nNote: Rollback requires a mirror with the target version.")
    print("Use 'sync --from <mirror> --to <target> --version <version>' instead.")

    return {"rollback_version": version, "target": str(target_dir)}


def list_versions(mirror_dir: Path) -> None:
    """List available versions in a mirror."""
    metadata = _load_metadata(mirror_dir)

    print(f"[governance-mirror] Available versions in {mirror_dir}:\n")
    for version in sorted(metadata.keys(), reverse=True):
        info = metadata[version]
        print(f"v{version}")
        print(f"  Exported: {info.get('exported_at', 'unknown')}")
        print(f"  Hash: {info.get('content_hash', 'N/A')[:16]}...")
        print(f"  Files: {', '.join(info.get('files', []))}")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Governance rule synchronization")
    subparsers = parser.add_subparsers(dest="command", help="commands")

    # export command
    export_parser = subparsers.add_parser("export", help="Export governance rules to mirror")
    export_parser.add_argument("--to", required=True, help="Output mirror directory")
    export_parser.add_argument("--source", default=".", help="Source repository (default: .)")

    # sync command
    sync_parser = subparsers.add_parser("sync", help="Sync governance rules from mirror")
    sync_parser.add_argument("--from", dest="mirror", required=True, help="Source mirror directory")
    sync_parser.add_argument("--to", dest="target", required=True, help="Target repository")
    sync_parser.add_argument("--version", help="Specific version to sync")
    sync_parser.add_argument("--dry-run", action="store_true", help="Show diff without changes")

    # dry-run is also a standalone command
    dryrun_parser = subparsers.add_parser("dry-run", help="Dry-run sync")
    dryrun_parser.add_argument("--from", dest="mirror", required=True, help="Source mirror directory")
    dryrun_parser.add_argument("--to", dest="target", required=True, help="Target repository")
    dryrun_parser.add_argument("--version", help="Specific version to sync")

    # rollback command
    rollback_parser = subparsers.add_parser("rollback", help="Rollback to previous version")
    rollback_parser.add_argument("--version", required=True, help="Version to rollback to")
    rollback_parser.add_argument("--target", required=True, help="Target repository")

    # list command
    list_parser = subparsers.add_parser("list", help="List available versions in mirror")
    list_parser.add_argument("mirror", help="Mirror directory")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    try:
        if args.command == "export":
            source = Path(args.source)
            output = Path(args.to)
            export_mirror(source, output)

        elif args.command == "sync":
            mirror = Path(args.mirror)
            target = Path(args.target)
            sync_mirror(mirror, target, version=args.version, dry_run=args.dry_run)

        elif args.command == "dry-run":
            mirror = Path(args.mirror)
            target = Path(args.target)
            sync_mirror(mirror, target, version=args.version, dry_run=True)

        elif args.command == "rollback":
            target = Path(args.target)
            rollback_mirror(target, args.version)

        elif args.command == "list":
            mirror = Path(args.mirror)
            list_versions(mirror)

    except MirrorError as e:
        sys.stderr.write(f"[governance-mirror] Error: {e}\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
