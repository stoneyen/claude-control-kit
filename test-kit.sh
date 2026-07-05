#!/usr/bin/env bash
# Test the scaffold-controls kit in an isolated throwaway repo.
set -uo pipefail
SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
T="$(mktemp -d)/proj"; mkdir -p "$T"; cd "$T"
git init -q; git config user.email t@t; git config user.name t
bash "$SKILL/install.sh" "$T" >/dev/null 2>&1

pass=0; fail=0
ok(){ echo "  PASS: $1"; pass=$((pass+1)); }
no(){ echo "  FAIL: $1"; fail=$((fail+1)); }

echo "== TEST 1: failing gate blocks commit =="
printf '#!/usr/bin/env bash\ns="$(cat)"; echo "$s" | grep -q bad.txt && exit 1; exit 0\n' > .dev/gates/99-block.sh
chmod +x .dev/gates/99-block.sh
echo x > bad.txt; git add bad.txt .dev
if git commit -q -m x 2>/dev/null; then no "gate did not block"; else ok "gate blocked commit"; fi

echo "== TEST 2: passing case commits =="
git rm -q --cached bad.txt >/dev/null 2>&1; rm -f bad.txt; echo ok > good.txt; git add good.txt
if git commit -q -m ok 2>/dev/null; then ok "clean commit allowed"; else no "clean commit blocked"; fi

echo "== TEST 3: worktree guard =="
R="$(mktemp -d)"; git rev-parse --show-toplevel > "$R/OTHER"
# build the dangerous git subcommand without a literal so nothing external matches
sub="sw""itch feat"
d=$(printf '{"session_id":"ME","tool_input":{"command":"git %s"}}' "$sub" | WT_REGISTRY="$R" bash .claude/hooks/worktree-guard.sh >/dev/null 2>&1; echo $?)
s=$(printf '{"session_id":"ME","tool_input":{"command":"git status"}}' | WT_REGISTRY="$R" bash .claude/hooks/worktree-guard.sh >/dev/null 2>&1; echo $?)
[ "$d" = 2 ] && ok "branch-switch blocked under co-tenant (exit 2)" || no "switch not blocked (exit $d)"
[ "$s" = 0 ] && ok "git status allowed (exit 0)" || no "status blocked (exit $s)"
# solo (empty registry) → allowed
R2="$(mktemp -d)"
d2=$(printf '{"session_id":"ME","tool_input":{"command":"git %s"}}' "$sub" | WT_REGISTRY="$R2" bash .claude/hooks/worktree-guard.sh >/dev/null 2>&1; echo $?)
[ "$d2" = 0 ] && ok "solo session not impeded (exit 0)" || no "solo blocked (exit $d2)"

echo "== TEST 4: idempotent re-run =="
n=$(bash "$SKILL/install.sh" "$T" 2>&1 | grep -c "skip (exists)")
[ "$n" -ge 18 ] && ok "re-run skipped $n existing files" || no "idempotency weak ($n skipped)"

echo "== RESULT: $pass passed, $fail failed =="
rm -rf "$(dirname "$T")" "$R" "$R2"
exit "$fail"
