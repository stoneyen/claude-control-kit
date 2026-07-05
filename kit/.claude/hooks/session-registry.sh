#!/usr/bin/env bash
# Shared helper for the worktree-isolation hooks (see .dev/adr on concurrency).
#
# A tiny file-backed registry of live Claude sessions, keyed by session id, whose
# content is the git worktree top-level each session is operating in. "Live" =
# heartbeat file touched within $WT_TTL seconds. Used to detect CO-TENANTS: two
# or more sessions sharing ONE working tree — the condition that lets concurrent
# `git checkout`/`stash` operations stomp each other's uncommitted work.
#
# Source this; it defines sr_register / sr_cotenants / sr_toplevel. No side
# effects on source (pure function defs). Project-agnostic.
WT_REGISTRY="${WT_REGISTRY:-${TMPDIR:-/tmp}/claude-session-registry}"
WT_TTL="${WT_TTL:-600}"   # seconds; a session gone quiet this long is dead

# top-level of the git worktree we're standing in ('' if not a repo).
sr_toplevel() { git rev-parse --show-toplevel 2>/dev/null || true; }

# file mtime in epoch seconds (portable macOS/Linux).
_sr_mtime() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0; }
_sr_now() { date +%s; }

# register/refresh THIS session's heartbeat. args: <session_id> <toplevel>
sr_register() {
  local sid="$1" top="$2"
  [ -n "$sid" ] || return 0
  mkdir -p "$WT_REGISTRY" 2>/dev/null || return 0
  printf '%s' "$top" >"$WT_REGISTRY/$sid" 2>/dev/null || true
}

# count OTHER live sessions whose registered top-level == $2. args: <session_id> <toplevel>
sr_cotenants() {
  local sid="$1" top="$2" now n=0
  [ -n "$top" ] || { echo 0; return 0; }
  [ -d "$WT_REGISTRY" ] || { echo 0; return 0; }
  now="$(_sr_now)"
  for f in "$WT_REGISTRY"/*; do
    [ -e "$f" ] || continue
    local other; other="$(basename "$f")"
    [ "$other" = "$sid" ] && continue
    local age=$(( now - $(_sr_mtime "$f") ))
    [ "$age" -gt "$WT_TTL" ] && { rm -f "$f" 2>/dev/null; continue; }   # prune dead
    [ "$(cat "$f" 2>/dev/null)" = "$top" ] && n=$(( n + 1 ))
  done
  echo "$n"
}
