# ADR-000 — Record architecture decisions

- **Date:** <YYYY-MM-DD>
- **Status:** Accepted

## Context

We want the *reasons* behind architectural choices to survive turnover and time,
and to be checkable before a change silently violates them. Undocumented
decisions get re-litigated or accidentally broken.

## Decision

Record every significant architectural decision as an ADR in `.dev/adr/`, one
file per decision (`ADR-NNN-short-title.md`), using this template's sections.
Maintain an index of accepted ADRs in `CLAUDE.md`. Before an architectural
change, consult the ADRs; to change a decision, supersede its ADR with a new one.

## Consequences

- Architectural intent is discoverable and enforceable (grep before change).
- A small per-decision writing cost; large saving in avoided drift + re-debates.

## Alternatives Considered

- **Wiki / tribal knowledge** — rots, unversioned, not next to the code.
- **Comments in code** — too local for cross-cutting decisions.

## Related Decisions

- (link related ADRs with their numbers)

## Notes

Template adapted from Michael Nygard's ADRs.
