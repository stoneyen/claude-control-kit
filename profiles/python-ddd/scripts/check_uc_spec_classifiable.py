"""Gate: every given UC spec YAML must be detect_type-classifiable (≠ UNKNOWN).

A spec the uc_executor engine cannot classify cannot be consumed by
`/execute-uc` — that is the breakpoint the unified UC flow
(`/new-requirement` writes the stub → `/execute-uc` generates code) closes.

Used by:
  - `.githooks/pre-commit` → `commit-gate.sh`, on **newly-added**
    `.dev/specs/**/usecase/*.yaml` (edits to legacy specs are not gated).
  - CI / manual: `uv run python scripts/check_uc_spec_classifiable.py <yaml>...`
    exits non-zero (and names the offenders) if any spec is UNKNOWN.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `from uc_executor.spec_loader import ...` when run as a script (mirrors
# the generate_skeleton.py shim).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from uc_executor.spec_loader import detect_type, load_spec


def classify_files(paths: list[Path]) -> list[Path]:
    """Return the subset of `paths` whose spec `detect_type` is UNKNOWN.

    A path that fails to parse, or whose top level is not a mapping, counts as
    unknown (it cannot carry a discriminator).
    """
    unknown: list[Path] = []
    for raw in paths:
        p = Path(raw)
        try:
            spec = load_spec(p)
        except Exception:
            unknown.append(p)
            continue
        if not isinstance(spec, dict) or detect_type(spec) == "UNKNOWN":
            unknown.append(p)
    return unknown


def main(argv: list[str]) -> int:
    unknown = classify_files([Path(a) for a in argv])
    if unknown:
        print(
            "UC spec(s) not classifiable by detect_type — add a `query:` "
            "(read) or `domainEvent(s):` (command) discriminator so /execute-uc "
            "can consume them:",
            file=sys.stderr,
        )
        for p in unknown:
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
