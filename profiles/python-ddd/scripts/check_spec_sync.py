"""Check that each `<aggregate>-spec.md` Attributes table is in sync with
the inline `aggregates:` block in every use-case YAML under the same aggregate.

This implements the "Future: pre-commit hook" called out in CLAUDE.md
§ "Sync rule: entity-spec.md ↔ use case yaml inline schema".

Violations:
  - `phantom-in-yaml`  : attribute present in YAML but missing from entity spec  (⛔ fail)
  - `type-mismatch`    : same attribute name, different type                     (⛔ fail)
  - `missing-in-yaml`  : entity attribute declared in NO use-case YAML's aggregates block (⚠ warn — a coverage gap; per-UC subset-inlining is by design and NOT flagged)

Usage:
    uv run python scripts/check_spec_sync.py                    # scan all
    uv run python scripts/check_spec_sync.py --strict           # warnings → failures
    uv run python scripts/check_spec_sync.py --aggregate card-account
    uv run python scripts/check_spec_sync.py --json

Exit code: 0 if no fail-level violations; 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent      # backend/
REPO = ROOT.parent                                  # claudews/
SPECS = REPO / ".dev" / "specs"


# ── Data model ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Attr:
    name: str
    type: str


@dataclass
class Drift:
    severity: str          # "fail" | "warn"
    rule: str              # "phantom-in-yaml" | "type-mismatch" | "missing-in-yaml"
    aggregate: str
    attribute: str
    entity_spec: str       # path
    use_case: str | None   # path (None when drift is about the entity itself)
    message: str


@dataclass
class Report:
    drifts: list[Drift] = field(default_factory=list)

    def add(self, d: Drift) -> None:
        self.drifts.append(d)

    def fail_count(self, strict: bool) -> int:
        return sum(1 for d in self.drifts if d.severity == "fail" or (strict and d.severity == "warn"))


# ── Entity spec parsing ────────────────────────────────────────────────────

_BACKTICK = re.compile(r"^`(.+?)`$")


def _strip_md_code(cell: str) -> str:
    """`foo` → foo, leaves bare text alone."""
    cell = cell.strip()
    m = _BACKTICK.match(cell)
    return m.group(1) if m else cell


def _normalize_type(t: str) -> str:
    """Strip backticks, trailing `?` (entity nullable convention), whitespace."""
    t = _strip_md_code(t)
    if t.endswith("?"):
        t = t[:-1]
    return t.strip()


def parse_entity_attributes(spec_md: Path) -> dict[str, Attr]:
    """Extract Attributes-section table → {name: Attr}."""
    text = spec_md.read_text(encoding="utf-8")
    section_match = re.search(r"^##\s+Attributes\s*$(.+?)(?=^##\s+\S)", text, re.M | re.S)
    if not section_match:
        return {}
    section = section_match.group(1)
    out: dict[str, Attr] = {}
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[0].lower() == "name" or set(cells[0]) <= {"-", ":", " "}:
            continue
        name = _strip_md_code(cells[0])
        typ = _normalize_type(cells[1])
        if not name or not typ:
            continue
        out[name] = Attr(name=name, type=typ)
    return out


def parse_entity_events(spec_md: Path) -> set[str]:
    """Extract 'Events Emitted' table → {event_short_name}.

    The entity spec lists events as `EventName` in the first column.
    Returns a set of short class names (last segment if dotted).
    """
    text = spec_md.read_text(encoding="utf-8")
    section_match = re.search(r"^##\s+Events Emitted\s*$(.+?)(?=^##\s+\S)", text, re.M | re.S)
    if not section_match:
        return set()
    section = section_match.group(1)
    out: set[str] = set()
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        if first.lower() in {"event", ""} or set(first) <= {"-", ":", " "}:
            continue
        name = _strip_md_code(first).split(".")[-1]
        if name:
            out.add(name)
    return out


# ── Use-case YAML parsing ──────────────────────────────────────────────────


def parse_yaml_aggregate_attrs(yaml_path: Path, aggregate_class: str) -> dict[str, Attr] | None:
    """Return {name: Attr} for the matching aggregate block, or None if absent."""
    spec = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    aggregates = spec.get("aggregates") or []
    for agg in aggregates:
        if agg.get("name") == aggregate_class:
            attrs = agg.get("attributes") or []
            out: dict[str, Attr] = {}
            for a in attrs:
                name = str(a.get("name", "")).strip()
                typ = _normalize_type(str(a.get("type", "")))
                if name and typ:
                    out[name] = Attr(name=name, type=typ)
            # A block with `ref:` and no inlined `attributes:` defers to the entity
            # spec (DRY) — that IS coverage, not an empty declaration. Return None so
            # it's treated like "nothing to sync-check here" rather than "declares 0
            # attrs" (which would mark every entity attr uncovered). Only blocks that
            # actually inline attributes are sync-checked.
            return out or None
    return None


def parse_yaml_domain_events(yaml_path: Path) -> dict[str, dict[str, Attr]]:
    """Return {event_name: {field_name: Attr}} for the YAML's domainEvents block."""
    spec = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    events = spec.get("domainEvents") or []
    out: dict[str, dict[str, Attr]] = {}
    for ev in events:
        # domainEvents entries come in two forms: a plain string (just the
        # event name, no inline attributes — the common form) or a dict
        # {name, attributes}. Accept both.
        if isinstance(ev, str):
            name = ev.strip()
            if name:
                out.setdefault(name, {})
            continue
        name = str(ev.get("name", "")).strip()
        if not name:
            continue
        attrs = ev.get("attributes") or []
        field_map: dict[str, Attr] = {}
        for a in attrs:
            fn = str(a.get("name", "")).strip()
            ft = _normalize_type(str(a.get("type", "")))
            if fn and ft:
                field_map[fn] = Attr(name=fn, type=ft)
        out[name] = field_map
    return out


# ── Sync check ─────────────────────────────────────────────────────────────


def _aggregate_class_from_dir(agg_dir: str) -> str:
    return "".join(p.capitalize() for p in agg_dir.split("-"))


def check_aggregate(
    entity_spec: Path,
    report: Report,
    cross_yaml_events: dict[str, list[tuple[str, dict[str, Attr]]]],
) -> None:
    # entity_spec is .dev/specs/<ctx>/<agg>/entity/<agg>-spec.md
    agg_dir = entity_spec.parent.parent.name        # 'card-account'
    aggregate_class = _aggregate_class_from_dir(agg_dir)
    usecase_dir = entity_spec.parent.parent / "usecase"

    entity_attrs = parse_entity_attributes(entity_spec)
    entity_events = parse_entity_events(entity_spec)
    if not entity_attrs:
        report.add(Drift(
            severity="warn",
            rule="entity-no-attributes",
            aggregate=aggregate_class,
            attribute="",
            entity_spec=str(entity_spec.relative_to(REPO)),
            use_case=None,
            message="Could not parse any rows from the entity Attributes table.",
        ))
        return

    if not usecase_dir.exists():
        return

    union_yaml_attrs: set[str] = set()   # attrs declared across ALL UC aggregates blocks
    saw_aggregate_block = False
    for yaml_path in sorted(usecase_dir.glob("*.yaml")):
        try:
            yaml_attrs = parse_yaml_aggregate_attrs(yaml_path, aggregate_class)
            yaml_events = parse_yaml_domain_events(yaml_path)
        except yaml.YAMLError as e:
            report.add(Drift(
                severity="fail",
                rule="yaml-parse-error",
                aggregate=aggregate_class,
                attribute="",
                entity_spec=str(entity_spec.relative_to(REPO)),
                use_case=str(yaml_path.relative_to(REPO)),
                message=f"YAML parse failed: {str(e).splitlines()[0]}",
            ))
            continue

        # ── Attributes ──
        if yaml_attrs is not None:
            for name in sorted(yaml_attrs.keys() - entity_attrs.keys()):
                report.add(Drift(
                    severity="fail", rule="phantom-in-yaml",
                    aggregate=aggregate_class, attribute=name,
                    entity_spec=str(entity_spec.relative_to(REPO)),
                    use_case=str(yaml_path.relative_to(REPO)),
                    message=f"YAML declares `{name}` but entity spec does not — add to entity spec, or remove from YAML.",
                ))
            for name in sorted(yaml_attrs.keys() & entity_attrs.keys()):
                e_t = entity_attrs[name].type
                y_t = yaml_attrs[name].type
                if e_t != y_t:
                    report.add(Drift(
                        severity="fail", rule="type-mismatch",
                        aggregate=aggregate_class, attribute=name,
                        entity_spec=str(entity_spec.relative_to(REPO)),
                        use_case=str(yaml_path.relative_to(REPO)),
                        message=f"`{name}`: entity={e_t!r}  vs  yaml={y_t!r}",
                    ))
            # Accumulate coverage across all UCs; the missing-in-yaml check is done
            # once per aggregate after the loop (see below), not per-UC — a UC
            # inlining only the subset it touches is by design (CLAUDE.md sync rule),
            # so per-UC "missing" is noise. The real signal is an entity attribute
            # declared in NO use-case at all.
            saw_aggregate_block = True
            union_yaml_attrs |= set(yaml_attrs.keys())

        # ── Events ──
        for ev_fqn, ev_fields in yaml_events.items():
            short = ev_fqn.split(".")[-1]
            # Record for cross-yaml check
            cross_yaml_events.setdefault(ev_fqn, []).append(
                (str(yaml_path.relative_to(REPO)), ev_fields)
            )
            # Entity-spec containment
            if entity_events and short not in entity_events:
                report.add(Drift(
                    severity="fail", rule="event-not-in-entity-spec",
                    aggregate=aggregate_class, attribute=short,
                    entity_spec=str(entity_spec.relative_to(REPO)),
                    use_case=str(yaml_path.relative_to(REPO)),
                    message=f"event `{ev_fqn}` declared in YAML but not listed in entity spec's 'Events Emitted' table.",
                ))

    # ── missing-in-yaml (per aggregate) ── an entity attribute declared in NO
    # use-case YAML's aggregates block = a real coverage gap. Per-UC "missing" is
    # noise (UCs inline only the subset they touch, by design), so we check the
    # union across all UCs once. Only when at least one UC declares the aggregate
    # (otherwise there's nothing to compare against).
    if saw_aggregate_block:
        for name in sorted(entity_attrs.keys() - union_yaml_attrs):
            report.add(Drift(
                severity="warn", rule="missing-in-yaml",
                aggregate=aggregate_class, attribute=name,
                entity_spec=str(entity_spec.relative_to(REPO)),
                use_case=None,
                message=f"`{name}` is in the entity spec but declared in no use-case YAML — uncovered (add it to whichever UC touches it).",
            ))


def check_cross_yaml_events(
    cross_yaml_events: dict[str, list[tuple[str, dict[str, Attr]]]],
    report: Report,
) -> None:
    """When same event FQN appears in >1 YAML, its attribute schemas must agree."""
    for ev_fqn, all_declarations in cross_yaml_events.items():
        # A plain-string domainEvents entry ("- pkg.events.X") is a name-only
        # reference — it asserts the event is emitted, not its field schema. Only
        # compare declarations that actually declare fields (the dict form).
        declarations = [(p, f) for (p, f) in all_declarations if f]
        if len(declarations) < 2:
            continue
        # Compare every later declaration against the first
        ref_path, ref_fields = declarations[0]
        for other_path, other_fields in declarations[1:]:
            ref_names = set(ref_fields.keys())
            oth_names = set(other_fields.keys())
            for missing in sorted(ref_names - oth_names):
                report.add(Drift(
                    severity="fail", rule="cross-yaml-event-mismatch",
                    aggregate="", attribute=missing,
                    entity_spec=ref_path,
                    use_case=other_path,
                    message=f"event `{ev_fqn}`: field `{missing}` declared in {ref_path} but missing here.",
                ))
            for extra in sorted(oth_names - ref_names):
                report.add(Drift(
                    severity="fail", rule="cross-yaml-event-mismatch",
                    aggregate="", attribute=extra,
                    entity_spec=ref_path,
                    use_case=other_path,
                    message=f"event `{ev_fqn}`: field `{extra}` here but not in {ref_path}.",
                ))
            for name in sorted(ref_names & oth_names):
                if ref_fields[name].type != other_fields[name].type:
                    report.add(Drift(
                        severity="fail", rule="cross-yaml-event-mismatch",
                        aggregate="", attribute=name,
                        entity_spec=ref_path,
                        use_case=other_path,
                        message=f"event `{ev_fqn}`: field `{name}` type mismatch — {ref_fields[name].type!r} vs {other_fields[name].type!r}.",
                    ))


def run(filter_agg: str | None) -> Report:
    report = Report()
    cross_yaml_events: dict[str, list[tuple[str, dict[str, Attr]]]] = {}
    for entity_spec in sorted(SPECS.rglob("entity/*-spec.md")):
        if filter_agg and filter_agg not in str(entity_spec):
            continue
        check_aggregate(entity_spec, report, cross_yaml_events)
    check_cross_yaml_events(cross_yaml_events, report)
    return report


# ── Output ─────────────────────────────────────────────────────────────────


def format_text(report: Report, strict: bool, verbose: bool) -> str:
    if not report.drifts:
        return "✅ Spec sync PASS — all entity spec ↔ YAML aggregates blocks are aligned.\n"

    by_sev_rule: dict[tuple[str, str], list[Drift]] = {}
    for d in report.drifts:
        by_sev_rule.setdefault((d.severity, d.rule), []).append(d)

    n_fail = sum(1 for d in report.drifts if d.severity == "fail")
    n_warn = sum(1 for d in report.drifts if d.severity == "warn")
    promote = strict and n_warn > 0
    status = "⛔ FAIL" if (n_fail or promote) else "✅ PASS-with-warnings"
    extra = "  (strict promotes warn→fail)" if promote else ""
    lines = [f"{status}  fail={n_fail}  warn={n_warn}{extra}", ""]

    NOISY_RULES = {"missing-in-yaml"}    # legitimate subsets — summarize only  # noqa: N806

    # FAIL section: always full detail
    for (sev, rule), items in sorted(by_sev_rule.items()):
        if sev != "fail":
            continue
        lines.append(f"── FAIL: {rule} ({len(items)}) ──")
        for d in items:
            lines.append(f"  {d.aggregate}.{d.attribute}")
            lines.append(f"    entity : {d.entity_spec}")
            if d.use_case:
                lines.append(f"    yaml   : {d.use_case}")
            lines.append(f"    → {d.message}")
        lines.append("")

    # WARN section: detail unless noisy rule
    for (sev, rule), items in sorted(by_sev_rule.items()):
        if sev != "warn":
            continue
        if rule in NOISY_RULES and not verbose:
            lines.append(f"── WARN: {rule} ({len(items)})  — suppressed, use --verbose to list ──")
            lines.append("")
            continue
        lines.append(f"── WARN: {rule} ({len(items)}) ──")
        for d in items:
            lines.append(f"  {d.aggregate}.{d.attribute}")
            lines.append(f"    entity : {d.entity_spec}")
            if d.use_case:
                lines.append(f"    yaml   : {d.use_case}")
            lines.append(f"    → {d.message}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Check entity-spec.md ↔ usecase yaml sync.")
    ap.add_argument("--aggregate", help="Filter by aggregate dir name (substring).")
    ap.add_argument("--strict", action="store_true", help="Treat warn-level drifts as failures.")
    ap.add_argument("--verbose", action="store_true", help="List every drift, including noisy `missing-in-yaml`.")
    ap.add_argument("--json", action="store_true", help="Emit JSON.")
    args = ap.parse_args()

    report = run(args.aggregate)

    if args.json:
        print(json.dumps(
            {
                "drifts": [d.__dict__ for d in report.drifts],
                "fail_count": report.fail_count(args.strict),
            },
            indent=2,
        ))
    else:
        print(format_text(report, args.strict, args.verbose))
    return 1 if report.fail_count(args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
