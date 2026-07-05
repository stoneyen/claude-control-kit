# .dev/gates/push/ — pre-push gates

Executable scripts here run on `git push` (via `.githooks/pre-push`). Use for
checks too slow per-commit but worth blocking a push (e.g. a test-drift diff
against the base branch, a full type build). Exit non-zero to block;
`git push --no-verify` bypasses. `.sample`/`.md` ignored.
