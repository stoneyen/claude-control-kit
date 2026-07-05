# .dev/gates/ — pluggable commit gates

Drop **executable** scripts here; `.claude/hooks/gate-runner.sh` runs each one
(sorted by name) on every `git commit` that stages files. A gate:

- receives the list of staged files on **stdin** and in **`$STAGED_FILES`**,
- exits **0** to pass, **non-zero** to BLOCK the commit (its stderr is shown).

Name them with a numeric prefix to order them: `10-lint.sh`, `20-typecheck.sh`,
`30-test-drift.sh`. `.sample` and `.md` files are ignored — copy a `.sample` to
a real name and `chmod +x` it. Keep gates **fast** (per-commit); push heavier
checks into `.dev/gates/push/` (run by `.githooks/pre-push`) or CI.

This is the project-specific layer of the harness: the worktree isolation and
the runner wiring are generic; what you check is up to you. Suggested gates:

| Gate | Blocks on |
|---|---|
| lint | linter errors on staged source files |
| typecheck | type errors |
| test-drift | a source change with no corresponding test change |
| arch/layering | import/dependency rules for your architecture |
| no-secrets | a staged file containing a credential pattern |

See `10-example-lint.sh.sample` for the shape.
