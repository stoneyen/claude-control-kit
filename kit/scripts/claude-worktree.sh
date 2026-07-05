#!/usr/bin/env bash
# Launch Claude Code in a FRESH per-session git worktree (concurrency discipline).
# The primary checkout stays on its branch and is never used for concurrent
# feature work; each session gets an isolated tree so two sessions can never
# stomp each other's uncommitted changes / branch state.
#
# Install once (~/.zshrc or ~/.bashrc), pointing at this script in your repo:
#   cw() { bash "/abs/path/to/repo/scripts/claude-worktree.sh" "$@"; }
# then start sessions with `cw [slug]` instead of `claude`.
#
# The worktree is auto-removed on exit IFF it has no uncommitted changes and no
# unpushed commits (so nothing is silently lost); otherwise it's kept and its
# path printed for you to finish/clean up manually.
set -uo pipefail

# Repo = the git toplevel this script lives in (so it's path-independent), unless
# WT_REPO overrides. Worktrees are created as siblings of the repo by default.
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${WT_REPO:-$(git -C "$_here" rev-parse --show-toplevel 2>/dev/null || echo "$_here")}"
WT_ROOT="${WT_ROOT:-$(dirname "$REPO")/$(basename "$REPO")-wt}"
SLUG="${1:-session}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BRANCH="wt/${SLUG}-${STAMP}-$$"
WT="${WT_ROOT}/${SLUG}-${STAMP}-$$"

command -v git >/dev/null || { echo "git not found" >&2; exit 1; }
[ -d "$REPO/.git" ] || { echo "WT_REPO ($REPO) is not a git repo" >&2; exit 1; }

mkdir -p "$WT_ROOT"
echo "▸ creating worktree $WT on new branch $BRANCH (base: current $REPO HEAD)"
git -C "$REPO" worktree add "$WT" -b "$BRANCH" >/dev/null || {
  echo "worktree add failed" >&2; exit 1; }

( cd "$WT" && claude "${@:2}" )
rc=$?

dirty="$(git -C "$WT" status --porcelain 2>/dev/null)"
unpushed="$(git -C "$WT" log --branches --not --remotes --oneline 2>/dev/null)"
if [ -z "$dirty" ] && [ -z "$unpushed" ]; then
  git -C "$REPO" worktree remove "$WT" --force >/dev/null 2>&1 && \
    git -C "$REPO" branch -D "$BRANCH" >/dev/null 2>&1
  echo "▸ clean — removed worktree $WT"
else
  echo "▸ KEPT worktree (uncommitted or unpushed work): $WT  [branch $BRANCH]" >&2
  echo "  finish it, then: git -C '$REPO' worktree remove '$WT'" >&2
fi
exit "$rc"
