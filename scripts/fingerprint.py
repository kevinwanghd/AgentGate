#!/usr/bin/env python3
"""
fingerprint.py — Cross-repository risk fingerprinting

Computes stable fingerprints for risk patterns based on pattern_type + normalized code shape.
The fingerprint must NOT include repository name, CR/MR ID, or file path.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata


# Version for fingerprint normalization algorithm
# Bump this when normalization logic changes to invalidate old fingerprints
FINGERPRINT_VERSION = "v1"


def normalize_code_for_fingerprint(code: str) -> str:
    """
    Normalize code snippet for fingerprint calculation.

    The goal is to produce the same fingerprint for semantically equivalent
    code patterns, even if variable names, whitespace, or literal values differ.

    Normalization steps:
    1. Strip whitespace
    2. Normalize Unicode
    3. Replace variable-like identifiers with placeholder
    4. Replace string literals with placeholder
    5. Replace numeric literals with placeholder
    """
    # Step 1: Basic whitespace normalization
    normalized = re.sub(r'\s+', ' ', code.strip())

    # Step 2: Unicode normalization
    normalized = unicodedata.normalize('NFKC', normalized)

    # Step 3: Replace string literals with placeholder
    # Handles: "string", 'string', """string""", '''string'''
    normalized = re.sub(r'"""[^"]*"""', '<STR>', normalized)
    normalized = re.sub(r"'''[^']*'''", '<STR>', normalized)
    normalized = re.sub(r'"(?:[^"\\]|\\.)*"', '<STR>', normalized)
    normalized = re.sub(r"'(?:[^'\\]|\\.)*'", '<STR>', normalized)

    # Step 4: Replace numeric literals with placeholder
    # Handles integers, floats, hex numbers
    normalized = re.sub(r'\b0x[0-9a-fA-F]+\b', '<NUM>', normalized)
    normalized = re.sub(r'\b\d+\.?\d*\b', '<NUM>', normalized)

    # Step 5: Replace common variable name patterns
    # Replace common variable naming patterns (camelCase, snake_case, PascalCase)
    # These are likely to differ between implementations
    normalized = re.sub(r'\b[a-z][a-z0-9_]*\b', '<VAR>', normalized, flags=re.IGNORECASE)

    # Step 6: Collapse multiple spaces
    normalized = re.sub(r'\s+', ' ', normalized)

    return normalized.strip()


def compute_fingerprint(pattern_type: str, code_snippet: str, version: str = FINGERPRINT_VERSION) -> str:
    """
    Compute a stable fingerprint for a risk pattern.

    The fingerprint is computed as SHA-256 of:
        version + ":" + pattern_type + ":" + normalized_code

    Returns the first 8 bytes (16 hex characters) of the hash.

    This ensures:
    - Same pattern_type + semantically similar code = same fingerprint
    - Different repositories with same pattern = same fingerprint
    - Repository name, CR/MR ID, file path NOT included
    """
    normalized = normalize_code_for_fingerprint(code_snippet)
    raw = f"{version}:{pattern_type}:{normalized}"
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    return digest[:16]


def compute_fingerprint_from_pending(pending_data: dict) -> str:
    """
    Compute fingerprint from pending lesson data.

    Uses pattern_type from pending and snippet from failure_context.
    """
    pattern_type = pending_data.get("pattern_type", "")
    snippet = pending_data.get("failure_context", {}).get("snippet", "")
    return compute_fingerprint(pattern_type, snippet)


# --- Fingerprint aggregation helpers ---

def merge_fingerprints(fps: list[str]) -> str:
    """
    Merge multiple fingerprints into a canonical ordering for comparison.

    Takes the lexicographically smallest fingerprint to ensure consistent
    ordering regardless of input order.
    """
    return sorted(fps)[0] if fps else ""


def are_fingerprints_equivalent(fps: list[str]) -> bool:
    """
    Check if multiple fingerprints are considered equivalent.

    Returns True if all fingerprints are identical.
    """
    if not fps:
        return True
    first = fps[0]
    return all(fp == first for fp in fps)


def fingerprint_file_name(pattern_type: str, code_snippet: str) -> str:
    """
    Generate a filename for a pending lesson based on fingerprint.

    Format: <pattern_type>-<fingerprint_short>.yml
    Example: sql-injection-a1b2c3d4e5f6g7h8.yml
    """
    fp = compute_fingerprint(pattern_type, code_snippet)
    # Sanitize pattern_type for filesystem
    safe_type = re.sub(r'[^a-z0-9-]', '-', pattern_type.lower())
    return f"{safe_type}-{fp}.yml"
