# python-ddd profile

Ports **the full MyWB ruleset** (a Python + Clean-Architecture/DDD + spec-driven
project) onto another project of the same shape. Layered on top of the base kit,
this profile adds the actual gate scripts + a spec-first hook + the DDD
conventions for `CLAUDE.md`.

Assumes the target has (or adopts) MyWB's layout:

```
<CCK_PY_ROOT>/            # e.g. backend/  (or "." if the repo root is the python root)
  pyproject.toml
  scripts/               # the profile copies its check scripts here
  src/<CCK_PKG>/         # domain/ application/ infrastructure/ interfaces/
  tests/
.dev/specs/<context>/<aggregate>/{entity,usecase}/   # spec-driven specs
```

## What it enforces (all hard-blocking, on humans + Claude)

| Gate | Blocks on | Portability |
|---|---|---|
| `20-ruff` | ruff errors on staged `.py` | any Python |
| `30-mypy` | type errors (when `src/<pkg>` staged) | any Python |
| `40-arch` | **Clean-Arch/DDD layering** violations — domain importing framework/other layers, `float` for money, aggregate-filter divergence, `/api/<resource>` router rule, etc. (validate_arch.py, 22 rules) | Python + DDD |
| `50-spec-sync` | entity-spec.md ↔ use-case yaml attribute drift | spec-driven |
| `55-uc-spec-classifiable` | a newly-added `usecase/*.yaml` with no `detect_type` discriminator | spec-driven |
| `60-test-drift` (push) | a behavioural source change with no matching test change | Python (adapt map) |

Plus `hooks/spec-first.sh` (PreToolUse edit-time nudge to write the spec before
editing a domain/UC/router file) and `CLAUDE-conventions.md` (the DDD
non-negotiables to graft into the project's CLAUDE.md).

## Configure

Copy `gates/profile.env.sample` → `.dev/gates/profile.env` and set:

```sh
CCK_PKG=yourpkg          # package name under src/<PKG>/
CCK_PY_ROOT=backend      # dir with scripts/ + src/ + pyproject  ("." if repo root)
CCK_PY="uv run python"   # python runner (or "python3", "poetry run python", …)
CCK_RUFF="uv run ruff"
CCK_MYPY="uv run mypy"
```

`validate_arch.py` reads `CCK_PKG`; the rest key off `CCK_PY_ROOT`.

## Adapt

- **`scripts/check_test_drift.py`** hardcodes the *source→test map* as regexes
  (`backend/src/<pkg>/domain/...` → its test, exempt lists, etc.). This map IS
  project-specific — edit the `WATCHED` / `EXEMPT` regex tables to match your
  layout.
- `validate_arch.py`'s **rules** (forbidden imports per layer, money suffixes,
  the `/api/` router rule) are MyWB's DDD conventions. Keep the ones you want;
  the layer names (`domain/application/infrastructure/interfaces`) are assumed.

## Verified

`validate_arch.py` (parameterized) reproduces MyWB's own result exactly —
`Gate 2.5 PASS, 22 rules` — when run against MyWB with `CCK_PKG=mywb`.
