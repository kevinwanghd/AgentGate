# Pending Lessons Schema

Pending lessons track candidate governance rules discovered from risk scans before they are reviewed and promoted/rejected.

## Storage Location

`.governance/pending-lessons/`

**Note:** This directory is **isolated** from `lessons/` and is NOT validated by `validate_lessons.py`. The `lessons/` directory requires `version: agentgate.io/lessons/v1` and has different semantics.

## File Naming

Each pending lesson is stored as a YAML file named by its fingerprint:
```
<pattern_type>-<fingerprint_short>.yml
```

Example: `sql-injection-a1b2c3d4.yml`

## Schema Definition

```yaml
id: string              # UUID v4 - globally unique identifier
pattern_type: string    # Risk type from registered_types (e.g., "sql-injection")
source_repo: string     # Repository name (e.g., "deliverhq", "agentgate")
source_ref: string      # Git ref where this was detected (e.g., "feature/auth-fix")
fingerprint: string     # Stable hash for cross-repo aggregation (see §Fingerprint below)
detected_at: string     # ISO 8601 datetime (e.g., "2026-08-30T12:00:00Z")
failure_context:
  file: string          # Relative file path
  line: integer          # Line number in the file
  snippet: string        # The detected code snippet (max 200 chars)
  diff_baseline: string # Git ref used for the scan (e.g., "origin/main")
evidence:
  rule_id: string       # The registered pattern type that triggered
  scan_summary: string  # Human-readable scan summary
  raw_diff_fragment: string # The exact diff context (max 500 chars)
regression: string       # Why this is a regression risk if not addressed
status: string           # One of: pending | confirmed | rejected | promoted
# --- fields below are set during/after review ---
reviewer: string         # (optional) GitHub username who reviewed
reviewed_at: string      # (optional) ISO 8601 datetime
decision_reason: string  # (optional) Why confirmed/rejected
classification: string   # (optional) One of: pattern | process | none
enforcement: string     # (optional) One of: soft | hard (auto-soft for generated)
promoted_to: string     # (optional) Path to the promoted lesson/pattern file
```

## Status Values

| Status | Description |
|--------|-------------|
| `pending` | Newly discovered, awaiting review |
| `confirmed` | Reviewed and accepted as a candidate rule |
| `rejected` | Reviewed and dismissed with reason |
| `promoted` | Applied to patterns/ or lessons/ |

## Validation Rules

1. **Required fields**: `id`, `pattern_type`, `source_repo`, `source_ref`, `fingerprint`, `detected_at`, `failure_context`, `evidence`, `regression`, `status`
2. **Valid status values**: `pending`, `confirmed`, `rejected`, `promoted`
3. **Unique IDs**: No two pending files may share the same `id`
4. **Fingerprint format**: Must be a 16-character hex string (first 8 chars of SHA-256)
5. **Datetime format**: ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`)

## Validation Command

```bash
python scripts/validate_pending.py --root .
```

## Fingerprint Algorithm

The fingerprint is computed as:
1. Extract `pattern_type`
2. Normalize the code snippet (strip whitespace, remove variable names, normalize string literals)
3. Compute SHA-256 of `pattern_type + normalized_snippet`
4. Take first 8 characters (16 hex digits)

**Important**: The fingerprint must NOT include:
- Repository name
- CR/MR ID
- File path (only pattern_type is included)
- Timestamp

This ensures the same risk pattern in different repos produces the same fingerprint.

## Aggregation

Multiple pending files with the same fingerprint represent the same risk pattern seen across different repositories. The aggregation view shows:
- Total occurrence count
- Unique repository count
- Last detection time
- Current status (most recent review decision)
