#!/usr/bin/env bash
# Project-agnostic COMMIT GATE runner. Runs every executable gate in
# `.dev/gates/` (sorted), passing the list of staged files on stdin AND in
# $STAGED_FILES. A gate exits 0 to pass, non-zero to BLOCK the commit. This is
# where a project plugs in its own lint / typecheck / arch / test-drift checks
# without the harness knowing anything language-specific.
#
# Called by .githooks/pre-commit (primary) and, as a fallback, by the Claude
# PreToolUse(Bash) dispatcher when core.hooksPath isn't set. Gates run only when
# the commit stages files (empty stage → trivially passes).
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
GATES_DIR="${GATES_DIR:-$ROOT/.dev/gates}"

STAGED_FILES="$(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null || true)"
export STAGED_FILES
[ -n "$STAGED_FILES" ] || exit 0
[ -d "$GATES_DIR" ] || exit 0

rc=0
for gate in "$GATES_DIR"/*; do
  [ -f "$gate" ] && [ -x "$gate" ] || continue
  case "$gate" in *.sample|*.md) continue;; esac
  name="$(basename "$gate")"
  if ! printf '%s\n' "$STAGED_FILES" | "$gate"; then
    echo "🚫 gate failed: $name" >&2
    rc=1
  fi
done
[ "$rc" -eq 0 ] || echo "── commit blocked by a gate above (this is the local quality gate) ──" >&2
exit "$rc"
