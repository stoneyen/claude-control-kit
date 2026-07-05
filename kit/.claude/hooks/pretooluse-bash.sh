#!/usr/bin/env bash
# Claude Code PreToolUse(Bash) dispatcher. Reads the hook JSON on stdin.
#  - `git commit`: FALLBACK gate. The git pre-commit hook (.githooks) is the
#    primary enforcer; if it's active (core.hooksPath=.githooks) we let it run
#    during the commit and exit 0 here (no double-run). If it's NOT active we
#    run the gate runner now and exit 2 to block.
#  - `git push` to main/master: non-blocking warn (prefer a PR).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd="$(jq -r '.tool_input.command // ""' 2>/dev/null || true)"

is_git_commit() { printf '%s' "$cmd" | grep -qE '\bgit\b[^;&|]*\bcommit\b'; }
is_git_push()   { printf '%s' "$cmd" | grep -qE '\bgit\b[^;&|]*\bpush\b'; }

if is_git_commit; then
  ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  hp="$(git -C "${ROOT:-.}" config --get core.hooksPath 2>/dev/null || true)"
  if [ "$hp" = ".githooks" ] && [ -x "${ROOT}/.githooks/pre-commit" ]; then
    exit 0   # git pre-commit hook will run the gate during the commit
  fi
  if ! "$HERE/gate-runner.sh"; then
    echo "(commit gate ran via the Claude fallback — git core.hooksPath is not .githooks; run: git config core.hooksPath .githooks)" >&2
    exit 2
  fi
  exit 0
fi

if is_git_push && printf '%s' "$cmd" | grep -qE '\b(main|master)\b'; then
  echo "⚠️  Pushing to main directly — prefer a PR. Proceeding." >&2
  exit 0
fi
exit 0
