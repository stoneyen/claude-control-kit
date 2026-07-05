<!-- Graft this section into the project's CLAUDE.md. These are the DDD
non-negotiables the arch gate (validate_arch.py) + review enforce. -->

## Architecture non-negotiables (Clean Architecture + DDD)

Layering — **domain has zero framework imports** (no SQLAlchemy, FastAPI,
Pydantic in `domain/`):

```
src/<pkg>/
  domain/          ← pure Python: entities, value objects, domain services, repo ports, events
  application/     ← use cases (orchestrate domain + repos), application DTOs
  infrastructure/  ← ORM models + repo impls, external clients, jobs
  interfaces/      ← HTTP routers, request/response DTOs
```

- **`Money` is a value object** — always `Decimal` + currency, **never `float`**.
  Cross-currency ops are explicit (`.convert(to, rate)`). Prevents most finance bugs.
- **Repositories are ports in `domain/`, impls in `infrastructure/`** — the
  domain stays testable without a database.
- **Domain models ≠ ORM models** — map between them in repository implementations.
- **DTOs at boundaries** — HTTP schemas map to/from domain entities; never expose
  ORM or domain models directly over HTTP.
- **Use cases are thin** — orchestrate repos + domain services + emit events.
  Business rules live in the domain, not the use case.
- **CQRS-lite reads** — read-heavy/cross-aggregate queries go through dedicated
  read-model query ports (raw SQL over the write tables), bypassing the write-side
  aggregates. Never load a write aggregate from a read query.
- **One source of truth for aggregate-state filters** — if the aggregate exposes
  "which children count as X", callers MUST use it; never re-inline the filter.
- **Normalize user-entered identifiers at the write boundary** (uppercase/strip/
  canonicalise inside the creating use case), so adapters can assume DB form.
- **Adapter selection fails loud** — `build_*_from_env` raises at start when a
  requested non-stub source's dependency isn't installed (never silently stub).
- **HTTP routers register at `/api/<resource>`** — the arch gate enforces this.
- **Diagnostic/audit VARCHAR columns default to `VARCHAR(64)`** (composed
  prefixes like `on_demand_refresh:yfinance` overflow tighter widths).

## Spec-first (spec-driven development) ⚠️

Before editing anything under `domain/`, `application/`, or
`interfaces/http/routers/`, update the matching spec under
`.dev/specs/<context>/<aggregate>/` FIRST and review it. Adding a field to an
aggregate/DTO/response, changing an invariant/state-machine, or adding a UC/route
all require a spec update first. The commit gate blocks a newly-added aggregate/UC
with no spec dir; `check_spec_sync` blocks entity-spec ↔ yaml drift.

## Test discipline

Every behavioural code change carries a matching test change in the same
commit/PR (the `test-drift` gate enforces this). Pure refactors / plumbing /
migrations are exempt — say so in the commit.
