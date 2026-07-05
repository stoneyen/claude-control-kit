#!/usr/bin/env bash
# PreToolUse(Edit|Write|MultiEdit) — co-tenant edit advisory (concurrency).
#
# When another live Claude session shares this working tree, editing here mixes
# your uncommitted changes with theirs. Default = WARN (exit 0, guidance to
# stderr) so work isn't hard-stopped mid-task; set WT_ENFORCE=block to
# hard-block edits and force a worktree. Refreshes this session's heartbeat.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/session-registry.sh"

input="$(cat 2>/dev/null || true)"
sid="$(printf '%s' "$input" | jq -r '.session_id // ""' 2>/dev/null || true)"
top="$(sr_toplevel)"
sr_register "$sid" "$top"

[ "${WT_ALLOW_SHARED_TREE:-0}" = "1" ] && exit 0
[ -n "$top" ] || exit 0

n="$(sr_cotenants "$sid" "$top")"
[ "${n:-0}" -ge 1 ] || exit 0

msg="⚠️  ${n} other live Claude session(s) share this working tree (${top}). Your edits will interleave with theirs — prefer an isolated 'git worktree add'."
if [ "${WT_ENFORCE:-warn}" = "block" ]; then
  { echo "🚫 BLOCKED (WT_ENFORCE=block): $msg"; \
    echo "Create a worktree and edit there, or set WT_ALLOW_SHARED_TREE=1 to override once."; } >&2
  exit 2
fi
echo "$msg" >&2
exit 0
