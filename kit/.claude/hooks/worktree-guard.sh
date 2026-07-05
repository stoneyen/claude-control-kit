#!/usr/bin/env bash
# PreToolUse(Bash) — shared-working-tree guard (see .dev/adr on concurrency).
#
# Blocks the exact operations that let two Claude sessions sharing ONE working
# tree stomp each other's uncommitted work: branch switches, stash, and hard
# resets — but ONLY when a live CO-TENANT session is detected in this same tree.
# A solo session is never impeded. The fix the message points at is a per-session
# git worktree (see scripts/claude-worktree.sh).
#
# Escape hatch (deliberate shared-tree maintenance): WT_ALLOW_SHARED_TREE=1.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/session-registry.sh"

input="$(cat 2>/dev/null || true)"
sid="$(printf '%s' "$input" | jq -r '.session_id // ""' 2>/dev/null || true)"
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null || true)"
top="$(sr_toplevel)"

# Always refresh our heartbeat first (this is the busiest hook → good cadence).
sr_register "$sid" "$top"

[ "${WT_ALLOW_SHARED_TREE:-0}" = "1" ] && exit 0
[ -n "$top" ] || exit 0

# Only the stomping ops, matched within one command segment (ignores `git log`).
# Path restores (`git checkout -- <file>`) touch one file, not HEAD → allowed.
seg='\bgit\b[^;&|]*'
is_dangerous() {
  if printf '%s' "$cmd" | grep -qE "${seg}\b(checkout|switch)\b"; then
    printf '%s' "$cmd" | grep -qE "${seg}\b(checkout|switch)\b[^;&|]* -- " || return 0
  fi
  printf '%s' "$cmd" | grep -qE "${seg}\bstash\b"              && return 0
  printf '%s' "$cmd" | grep -qE "${seg}\breset\b[^;&|]*--hard" && return 0
  return 1
}
is_dangerous || exit 0

n="$(sr_cotenants "$sid" "$top")"
[ "${n:-0}" -ge 1 ] || exit 0   # solo in this tree → allowed

cat >&2 <<EOF
🚫 BLOCKED: branch-switch/stash/reset --hard in a SHARED working tree.
${n} other live Claude session(s) are operating in this same tree:
  ${top}
Switching branches or stashing here rips uncommitted work out from under them.

Do your work in an isolated worktree instead:
  git worktree add ../$(basename "$top")-<slug> -b <branch>
  # then edit + commit using absolute paths under that worktree
When done: git worktree remove <path>

Deliberate shared-tree maintenance? Re-run once with WT_ALLOW_SHARED_TREE=1.
EOF
exit 2
