# claude-control-kit

A reusable, **language-agnostic** governance harness for Claude Code projects,
distilled from a well-governed reference project. Gives any repo the same
controls with one command:

- **Worktree-per-session isolation** — `PreToolUse` guards that hard-block
  `git checkout/switch/stash/reset --hard` when a live co-tenant Claude session
  shares the working tree (solo sessions unaffected), a `SessionStart` advisory,
  and a `claude-worktree.sh` launcher. Stops concurrent sessions stomping each
  other's branch/stash state.
- **Pluggable gate harness** — `.githooks/pre-commit` + `pre-push` and a
  `PreToolUse(Bash)` fallback run a language-agnostic `gate-runner.sh` over
  `.dev/gates/*` (staged-file-scoped). You drop your own lint / typecheck /
  test-drift / arch gates into `.dev/gates/`.
- **`.dev/` sources of truth** — `adr/` (+ ADR-000 template), `lessons/`,
  `tasks/`, `gates/`, each with a README explaining the discipline.
- **Templates** (installed as `.sample`, never overwriting): `CLAUDE.md.sample`,
  `.claude/settings.hooks.json` (hooks block to merge), and a CI
  `checks.yml.sample` carrying the **workflow_run deploy-gate** pattern (a red
  `checks` blocks the deploy).

## Install into a project

```bash
bash /path/to/claude-control-kit/install.sh [target-repo]   # default: $PWD
```

The installer is **idempotent and non-destructive** — it skips any file that
already exists and only ever *sets* `core.hooksPath=.githooks`. It does not
touch `.claude/settings.json` or `CLAUDE.md`; finish those by hand (or via the
`scaffold-controls` Claude Code skill, which does the merge with judgment).

After installing:

1. Merge `.claude/settings.hooks.json` into `.claude/settings.json`.
2. Fill `CLAUDE.md` from `CLAUDE.md.sample` (stack, conventions, ADR index).
3. Add real gates: copy `.dev/gates/10-example-lint.sh.sample` → `10-lint.sh`,
   `chmod +x`, adapt to your toolchain. Add typecheck / test-drift / arch gates.
4. Copy `.github/workflows/checks.yml.sample` → `checks.yml`; wire real CI. For
   a deploy a red gate must block, use the `workflow_run` pattern documented in it.
5. Add a `cw` shell function to your rc pointing at the repo's
   `scripts/claude-worktree.sh`; start sessions with `cw` instead of `claude`.

## Profiles — ready-made rulesets

The base kit is deliberately empty of project-specific gates. **Profiles** layer
a full, ready-made ruleset on top for a given project shape:

```bash
bash install.sh --profile python-ddd [--py-root backend] [target-repo]
```

- **`python-ddd`** — the full MyWB ruleset for a Python + Clean-Architecture/DDD
  + spec-driven project: `ruff` · `mypy` · **arch/layering lint** (validate_arch,
  22 rules — domain-imports-framework, float-money, `/api/` router rule, …) ·
  entity-spec↔yaml sync · UC-spec classifiability · test-drift, plus a spec-first
  hook and the DDD conventions for `CLAUDE.md`. Configure via `.dev/gates/profile.env`
  (`CCK_PKG` = your `src/<pkg>`). Parameterized + verified against MyWB. See
  [`profiles/python-ddd/README.md`](profiles/python-ddd/README.md).

Add more profiles under `profiles/<name>/` (a `gates/` dir + optional scripts +
a README); `install.sh --profile <name>` copies them in.

## The Claude Code skill

`~/.claude/skills/scaffold-controls/` wraps this kit as a `/scaffold-controls`
skill so, inside any project, Claude runs the installer and does the judgment
steps (settings merge, CLAUDE.md fill) for you. The skill points at this repo;
this repo is the source of truth.

## Design

Generic controls (worktree isolation, gate wiring, `.dev/` discipline) work out
of the box. Project-specific checks are **not** shipped — they live in
`.dev/gates/` per project. That split is deliberate: the harness is universal;
what you enforce is yours.

Prereqs on the machine: `bash`, `git`, `jq`.

## Slides: the engineering playbook

[`docs/mywb-engineering-playbook.html`](docs/mywb-engineering-playbook.html) — a
self-contained slide deck explaining the governance model behind this kit
(sources of truth, CLAUDE.md, the enforcement harness, spec-first, CI/CD deploy
gate, worktree isolation) so a team can understand *why* and apply it to their
own projects. Open in a browser; arrow keys / space to navigate, `f` for
fullscreen. 中文版:[`docs/mywb-engineering-playbook.zh.html`](docs/mywb-engineering-playbook.zh.html)。
