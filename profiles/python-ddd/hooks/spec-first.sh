#!/usr/bin/env bash
# Claude Code PreToolUse(Edit|Write|MultiEdit) — spec-first NUDGE (non-blocking).
# CLAUDE.md mandates a matching .dev/specs entry before creating a NEW file under
# domain/ application/ interfaces/http/routers/. This can't be proven from the
# path alone (could be adding to an existing aggregate), so it WARNS, never blocks:
# allow the edit, inject a reminder so Claude consciously confirms the spec.
set -uo pipefail

# Buffer stdin once so we can read both file_path and content from it.
payload="$(cat)"
emit() {  # emit "<message>" — non-blocking allow + reminder to model + user
  printf '%s\n' "{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"allow\",\"additionalContext\":$(jq -Rn --arg m "$1" '$m')},\"systemMessage\":$(jq -Rn --arg m "$1" '$m')}"
}

f="$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""' 2>/dev/null || true)"

# --- UC spec yaml: nudge if it lacks a detect_type discriminator -------------
# A UC spec with neither `query:` (read) nor `domainEvent(s):` (command) is
# detect_type-UNKNOWN → /execute-uc can't consume it and the commit gate blocks a
# newly-added UNKNOWN spec. Only checked on Write (full content available).
case "$f" in
  *.dev/specs/*/usecase/*.yaml)
    content="$(printf '%s' "$payload" | jq -r '.tool_input.content // ""' 2>/dev/null || true)"
    if [ -n "$content" ] && ! printf '%s' "$content" | grep -qE '^(query|domainEvent|domainEvents):'; then
      emit "UC spec ($(basename "$f")) has no detect_type discriminator — add 'query:' (read) or 'domainEvents:' (command) so /execute-uc can consume it. A newly-added UNKNOWN UC spec is blocked by the commit gate."
    fi
    exit 0 ;;
esac

case "$f" in
  *backend/src/mywb/domain/*|*backend/src/mywb/application/*|*backend/src/mywb/interfaces/http/routers/*) ;;
  *) exit 0 ;;
esac
# Only NEW files — editing an existing file under these dirs is fine.
[ -e "$f" ] && exit 0

emit "spec-first (CLAUDE.md): you are creating a NEW file under domain/application/routers ($(basename "$f")). Confirm a matching .dev/specs/<context>/<aggregate>/ spec exists and is updated FIRST (run check_spec_sync.py). If this is a new UC, use /execute-uc."
exit 0
