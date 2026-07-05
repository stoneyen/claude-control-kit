#!/usr/bin/env bash
# Installs the scaffold-controls kit into a target git repo (default: $PWD).
# Copies the generic control machinery, makes hooks executable, activates
# .githooks, and drops sample/template files WITHOUT clobbering existing files.
# It does NOT touch .claude/settings.json or CLAUDE.md — the skill merges those
# with judgment (see SKILL.md). Idempotent; safe to re-run.
#
# Usage:  bash install.sh [target-repo-dir]
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/kit" && pwd)"
TARGET="${1:-$PWD}"
cd "$TARGET"
git rev-parse --show-toplevel >/dev/null 2>&1 || { echo "ERROR: $TARGET is not a git repo" >&2; exit 1; }
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

copy() { # copy src → dest, never overwrite; report action
  local src="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [ -e "$dest" ]; then echo "  skip (exists): $dest"; else cp "$src" "$dest"; echo "  add: $dest"; fi
}

echo "== scaffold-controls → $ROOT =="

# 1. Worktree isolation + gate harness hooks (executable)
for h in session-registry session-start-register worktree-guard worktree-guard-edit pretooluse-bash gate-runner; do
  copy "$KIT/.claude/hooks/$h.sh" ".claude/hooks/$h.sh"; chmod +x ".claude/hooks/$h.sh"
done

# 2. git hooks + activate
copy "$KIT/.githooks/pre-commit" ".githooks/pre-commit"; chmod +x .githooks/pre-commit
copy "$KIT/.githooks/pre-push"   ".githooks/pre-push";   chmod +x .githooks/pre-push
git config core.hooksPath .githooks && echo "  set: core.hooksPath=.githooks"

# 3. worktree launcher
copy "$KIT/scripts/claude-worktree.sh" "scripts/claude-worktree.sh"; chmod +x scripts/claude-worktree.sh

# 4. .dev/ scaffold (READMEs, ADR-000 template, example gate) — no clobber
copy "$KIT/.dev/adr/README.md"                              ".dev/adr/README.md"
copy "$KIT/.dev/adr/ADR-000-record-architecture-decisions.md" ".dev/adr/ADR-000-record-architecture-decisions.md"
copy "$KIT/.dev/lessons/README.md"                          ".dev/lessons/README.md"
copy "$KIT/.dev/tasks/README.md"                            ".dev/tasks/README.md"
copy "$KIT/.dev/gates/README.md"                            ".dev/gates/README.md"
copy "$KIT/.dev/gates/10-example-lint.sh.sample"            ".dev/gates/10-example-lint.sh.sample"
copy "$KIT/.dev/gates/push/README.md"                       ".dev/gates/push/README.md"

# 5. templates (kept as .sample so they never silently overwrite real files)
copy "$KIT/.claude/settings.hooks.json"          ".claude/settings.hooks.json"
copy "$KIT/.github/workflows/checks.yml.sample"  ".github/workflows/checks.yml.sample"
copy "$KIT/CLAUDE.md.sample"                      "CLAUDE.md.sample"

cat <<'NEXT'

== installed. Next (the skill does 1–2 for you) ==
1. Merge .claude/settings.hooks.json into .claude/settings.json (append the
   PreToolUse/Edit/SessionStart entries; create the file if absent).
2. Create/extend CLAUDE.md (use CLAUDE.md.sample) — fill placeholders from the
   real project; add an ADR index.
3. Add real gates: copy .dev/gates/10-example-lint.sh.sample → 10-lint.sh,
   chmod +x, wire your lint/typecheck/test-drift/arch checks.
4. Copy .github/workflows/checks.yml.sample → checks.yml; wire real CI steps.
   For a deploy that a red gate must block, use the workflow_run pattern in it.
5. Add a `cw` shell function to your ~/.zshrc:
     cw() { bash "REPO_ABS_PATH/scripts/claude-worktree.sh" "$@"; }
   Start sessions with `cw` instead of `claude`.
NEXT
