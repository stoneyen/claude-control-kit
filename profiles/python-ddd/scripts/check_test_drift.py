#!/usr/bin/env python3
"""Test-drift guard — flag source changes that lack a corresponding test change.

The codebase already guards *spec* drift (`check_spec_sync.py`) and *architecture*
(`validate_arch.py`). This guards *test* drift: the failure mode where behaviour
changes but its test is left stale, so the suite keeps passing while no longer
describing the code.

Given a git diff range, it partitions the changed files into watched SOURCE and
TEST files (backend + frontend), then for every non-exempt SOURCE change checks
whether a *corresponding* TEST file changed in the same diff. Correspondence is
satisfied by any of:

  * STEM   — the source stem is a substring of (or equal to) a changed test's
             stem, or vice versa: `add_holding.py` ↔ `test_add_holding.py`,
             `bank_accounts.py` ↔ `test_bank_accounts_router.py`,
             `AccountsFilterBar.tsx` ↔ `AccountsFilterBar.test.tsx`    (strong)
  * FUZZ   — an HTTP-router source change when the API fuzz suite changed (it
             covers every GET + allowlisted write endpoint)            (medium)
  * GROUP  — any test under the same bounded context / area changed
             (`spending`, `accounts`, `credit_card`, …)               (weak)

Unsatisfied source changes are reported. Default exit is 0 (advisory); `--strict`
exits 1 so CI / a pre-push hook can block. Exempt surfaces (migrations, __init__,
pure data/enum/types, DI wiring, configs, generated assets) never require a test.

Usage (from backend/, like the other guards):
    uv run python scripts/check_test_drift.py                 # main...HEAD
    uv run python scripts/check_test_drift.py --working       # uncommitted too
    uv run python scripts/check_test_drift.py --base origin/main --strict
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

# --- repo root (this file lives at <root>/backend/scripts/) -----------------
ROOT = Path(__file__).resolve().parents[2]


# --- classification ---------------------------------------------------------
# A changed file matching a WATCHED pattern is expected to come with a test
# change, UNLESS it also matches EXEMPT.
_WATCHED = (
    re.compile(r"^backend/src/mywb/domain/.+\.py$"),
    re.compile(r"^backend/src/mywb/application/.+\.py$"),
    re.compile(r"^backend/src/mywb/interfaces/http/routers/.+\.py$"),
    re.compile(
        r"^backend/src/mywb/infrastructure/"
        r"(repositories|parsers|brokers|quote|llm)/.+\.py$"
    ),
    re.compile(r"^frontend/src/.+\.tsx?$"),
)

_EXEMPT = (
    re.compile(r"/__init__\.py$"),
    re.compile(r"/conftest\.py$"),
    re.compile(r"^backend/alembic/"),
    # pure data / contracts / wiring — no behaviour to test in isolation
    re.compile(r"/(ids|events|errors|enums|constants|types|schema|dto)\.py$"),
    # application read-model view DTOs — frozen dataclasses, exercised by the
    # read-model/UC tests that construct them, never tested in isolation
    re.compile(r"^backend/src/mywb/application/.+/views\.py$"),
    re.compile(r"^backend/src/mywb/application/ports/"),
    re.compile(r"^backend/src/mywb/application/shared/unit_of_work\.py$"),  # DI wiring
    re.compile(r"^backend/src/mywb/interfaces/http/(deps|app)\.py$"),
    # frontend non-logic
    re.compile(r"\.d\.ts$"),
    re.compile(r"^frontend/src/(main\.tsx|vite-env\.d\.ts|_ds-entry\.ts)$"),  # barrel/registry
    # Settings is a pure panel-composition page: it only imports + renders
    # already-tested Settings panels (each carries its own vitest) and holds no
    # logic of its own, so adding/removing a panel is test-neutral. (Routes with
    # real logic — Investments, Overview — are NOT exempt; they have own tests.)
    re.compile(r"^frontend/src/routes/Settings\.tsx$"),
    re.compile(r"^frontend/src/types/"),
    # static mock fixtures for fetchOrMock — data, not behaviour
    re.compile(r"^frontend/src/lib/api/mock-data\.ts$"),
    re.compile(r"^frontend/src/i18n/"),
    re.compile(r"\.(test|spec)\.tsx?$"),  # tests aren't "source"
    re.compile(r"^frontend/src/test/"),  # vitest setup/infra (like conftest.py)
)

_TEST = (
    re.compile(r"^backend/tests/.+/test_[^/]+\.py$"),
    re.compile(r"^backend/tests/fuzz/.+\.py$"),
    re.compile(r"^frontend/src/.+\.test\.tsx?$"),
    re.compile(r"^frontend/e2e/.+\.spec\.ts$"),
)

# Bounded-context / area tokens used for the weak GROUP correspondence.
_BACKEND_CONTEXTS = {
    "accounts", "credit_card", "incoming_email", "investments",
    "issuer_credentials", "shared_access", "spending", "system", "warren",
    "shared",
}


def _matches(path: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(path) for p in patterns)


def _stem(path: str) -> str:
    name = Path(path).name
    name = re.sub(r"\.(test|spec)\.tsx?$", "", name)
    name = re.sub(r"\.(py|tsx?)$", "", name)
    name = re.sub(r"^test_", "", name)
    return name


def _tokens(path: str) -> set[str]:
    """Context/area tokens for GROUP matching."""
    parts = set(Path(path).parts)
    toks = parts & _BACKEND_CONTEXTS
    # frontend: the immediate parent dir is a decent area token
    p = Path(path)
    if path.startswith("frontend/") and len(p.parts) >= 2:
        toks.add(p.parent.name)
    return toks


def _changed_files(base: str | None, working: bool) -> list[str]:
    cmds: list[list[str]] = []
    if base:
        cmds.append(["git", "-C", str(ROOT), "diff", "--name-only", f"{base}...HEAD"])
    if working or not base:
        # When a base ref is provided (e.g. commit-gate: --working --base main)
        # only include STAGED changes and the committed branch history — NOT
        # unstaged working-tree diffs or untracked files.  In a parallel-agent
        # setup those contain work-in-progress from other sessions that is NOT
        # part of this commit.  commit-gate.sh says explicitly: "this commit is
        # NOT coupled to unrelated source drift … in a parallel session."
        # Without a base ref (pure --working / advisory mode) include everything.
        if not base:
            cmds.append(["git", "-C", str(ROOT), "diff", "--name-only", "HEAD"])
            cmds.append(
                ["git", "-C", str(ROOT), "ls-files", "--others", "--exclude-standard"]
            )
        cmds.append(["git", "-C", str(ROOT), "diff", "--name-only", "--cached"])
    out: set[str] = set()
    for cmd in cmds:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        out.update(line for line in res.stdout.splitlines() if line.strip())
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="main", help="diff base ref (default: main)")
    ap.add_argument(
        "--working", action="store_true",
        help="also include uncommitted (working tree + staged) changes",
    )
    ap.add_argument(
        "--strict", action="store_true",
        help="exit 1 if any source change lacks a corresponding test change",
    )
    args = ap.parse_args()

    # If HEAD has no `base` ancestor (fresh repo / detached), fall back to working.
    base: str | None = args.base
    probe = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", str(base)],
        capture_output=True, text=True, check=False,
    )
    if probe.returncode != 0:
        base = None

    changed = _changed_files(base, args.working)
    sources = [
        f for f in changed
        if _matches(f, _WATCHED) and not _matches(f, _EXEMPT)
    ]
    tests = [f for f in changed if _matches(f, _TEST)]

    if not sources:
        print("✅ test-drift: no watched source changes in range — nothing to check.")
        return 0

    test_stems = [_stem(t) for t in tests]
    test_tokens: set[str] = set()
    for t in tests:
        test_tokens |= _tokens(t)
    fuzz_changed = any(t.startswith("backend/tests/fuzz/") for t in tests)
    # Combined text of every changed test — backend tests live under
    # tests/unit/<layer>/ (not by bounded context), and a test is often named
    # for the aggregate, not the UC, so a changed test that imports/references
    # the source by its module stem credits it even when names/paths don't line
    # up. Read once.
    test_corpus = ""
    for t in tests:
        try:
            test_corpus += (ROOT / t).read_text(errors="ignore")
        except OSError:
            pass

    def _stem_match(src_stem: str) -> bool:
        # Substring either way, length-guarded so very short stems don't match
        # noise.  Length-3 stems (e.g. "end") are common enough to deserve
        # substring matching against longer test stems ("end_chat_session").
        if len(src_stem) < 3:
            return src_stem in test_stems
        return any(
            src_stem in ts or (len(ts) >= 3 and ts in src_stem)
            for ts in test_stems
        )

    is_router = re.compile(r"interfaces/http/routers/")

    unsatisfied: list[str] = []
    rows: list[tuple[str, str]] = []
    def _camel(snake: str) -> str:
        return "".join(p[:1].upper() + p[1:] for p in snake.split("_"))

    for s in sources:
        stem = _stem(s)
        if _stem_match(stem):
            rows.append((s, "✅ stem"))
        elif len(stem) >= 6 and (
            stem in test_corpus or _camel(stem) in test_corpus
        ):
            # a changed test references it by module path or class name
            rows.append((s, "~ ref"))
        elif fuzz_changed and is_router.search(s):
            rows.append((s, "~ fuzz"))
        elif _tokens(s) & test_tokens:
            rows.append((s, "~ group"))
        else:
            rows.append((s, "❌ none"))
            unsatisfied.append(s)

    print(f"== test-drift guard ==  base={base or '(working)'}  "
          f"sources={len(sources)} tests-changed={len(tests)}")
    for path, status in rows:
        print(f"  {status}  {path}")

    if unsatisfied:
        print(
            f"\n⚠️  {len(unsatisfied)} source change(s) with NO corresponding test "
            f"change:\n" + "\n".join(f"    - {u}" for u in unsatisfied)
        )
        print(
            "\n  Add/adjust a test (unit / fuzz / e2e), or — if the change is "
            "genuinely test-neutral\n  (pure refactor, plumbing) — note that in "
            "the commit/PR. Extend _EXEMPT for a\n  durable carve-out."
        )
        if args.strict:
            return 1
    else:
        print("\n✅ every watched source change has a corresponding test change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
