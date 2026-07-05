#!/usr/bin/env bash
# SessionStart — register this session in the worktree registry + advise if it
# is entering a working tree another live session already occupies. Advisory
# only; the PreToolUse guards do the blocking.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/session-registry.sh"

input="$(cat 2>/dev/null || true)"
sid="$(printf '%s' "$input" | jq -r '.session_id // ""' 2>/dev/null || true)"
top="$(sr_toplevel)"

n="$(sr_cotenants "$sid" "$top")"   # check BEFORE registering ourselves
sr_register "$sid" "$top"

if [ -n "$top" ] && [ "${n:-0}" -ge 1 ]; then
  cat <<EOF
⚠️  This working tree (${top}) already has ${n} live Claude session(s) in it.
Concurrent sessions in one tree stomp each other on git checkout/stash. For
isolated work, launch via the 'cw' worktree helper (scripts/claude-worktree.sh)
or run 'git worktree add ../$(basename "$top")-<slug> -b <branch>' and work there.
Branch-switch/stash/reset --hard here are blocked while a co-tenant is live.
EOF
fi
exit 0
