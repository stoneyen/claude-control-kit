"""Gate 2.5 — architectural lint for MyWB backend.

Enforces ADR-002 (Clean Architecture layering), ADR-003 (Money as Decimal),
ADR-006 (Domain Events as immutable dataclasses), ADR-007 (Audit on row /
TZ-aware datetime), and the patterns documented under
`scripts/uc_executor/patterns/`.

Usage:
    uv run python scripts/validate_arch.py
    uv run python scripts/validate_arch.py --paths src/mywb/domain tests/unit
    uv run python scripts/validate_arch.py --json

Exit code 0 if clean, 1 if any violation found.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Package under test — `src/<PKG>/`. Set CCK_PKG when this project's package is
# not `mywb` (the profile installer writes it into the gate wrapper's env).
PKG = os.environ.get("CCK_PKG", "mywb")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / PKG
TESTS = ROOT / "tests"

# ── Layer-import rule data ────────────────────────────────────────────────

DOMAIN_FORBIDDEN_EXTERNAL = frozenset(
    {"sqlalchemy", "fastapi", "pydantic", "arq", "redis", "alembic"}
)
APPLICATION_FORBIDDEN_EXTERNAL = frozenset({"fastapi", "pydantic", "alembic"})
INFRASTRUCTURE_FORBIDDEN_EXTERNAL = frozenset({"fastapi"})

INTERNAL_FORBIDDEN: dict[str, frozenset[str]] = {
    "domain": frozenset({f"{PKG}.application", f"{PKG}.infrastructure", f"{PKG}.interfaces"}),
    "application": frozenset({f"{PKG}.interfaces"}),
    "infrastructure": frozenset({f"{PKG}.interfaces"}),
}

# Narrow seams. Every entry is a documented exception, not "I couldn't fix it".
EXTERNAL_EXCEPTIONS: set[tuple[str, str, str]] = {
    ("application", "shared/unit_of_work.py", "sqlalchemy"),
}

# ── Money-typing rule data ────────────────────────────────────────────────

EXACT_MONEY_NAMES = frozenset({
    "amount", "balance", "outstanding", "price",
    "credit_limit", "minimum_payment", "statement_balance",
})
MONEY_SUFFIXES = ("_amount", "_balance", "_price", "_payment")


# ── Data model ─────────────────────────────────────────────────────────────


@dataclass
class Violation:
    file: str
    line: int
    rule: str
    message: str


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)

    def add(self, v: Violation) -> None:
        self.violations.append(v)

    @property
    def ok(self) -> bool:
        return not self.violations


# ── Path / AST helpers ────────────────────────────────────────────────────


def layer_of(path: Path) -> str | None:
    """Return the top-level layer for a file: one of
    'domain' / 'application' / 'infrastructure' / 'interfaces' / 'tests',
    or None if outside the known roots."""
    try:
        rel = path.relative_to(SRC).parts
        return rel[0] if rel else None
    except ValueError:
        pass
    try:
        path.relative_to(TESTS)
        return "tests"
    except ValueError:
        return None


def rel_within_layer(path: Path, layer: str) -> str:
    if layer == "tests":
        return str(path.relative_to(TESTS)).replace("\\", "/")
    return str(path.relative_to(SRC / layer)).replace("\\", "/")


def file_rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def iter_imports(tree: ast.AST) -> Iterable[tuple[int, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                yield node.lineno, node.module


def iter_from_imports(tree: ast.AST) -> Iterable[tuple[int, str, list[str]]]:
    """Yield (lineno, module, [names]) for `from X import a, b`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.lineno, node.module, [a.name for a in node.names]


def top_pkg(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def matches_internal_forbid(dotted: str, forbids: frozenset[str]) -> str | None:
    for prefix in forbids:
        if dotted == prefix or dotted.startswith(prefix + "."):
            return prefix
    return None


def class_has_base(cls_node: ast.ClassDef, base_name: str) -> bool:
    for b in cls_node.bases:
        if isinstance(b, ast.Name) and b.id == base_name:
            return True
        if isinstance(b, ast.Attribute) and b.attr == base_name:
            return True
    return False


def find_decorator(cls_node: ast.ClassDef, name: str) -> tuple[bool, ast.Call | None]:
    """Return (present, call) for `@<name>(...)`. `call` is None for bare `@<name>`."""
    for d in cls_node.decorator_list:
        if isinstance(d, ast.Call):
            f = d.func
            if (isinstance(f, ast.Name) and f.id == name) or (
                isinstance(f, ast.Attribute) and f.attr == name
            ):
                return True, d
        elif isinstance(d, ast.Name) and d.id == name:
            return True, None
        elif isinstance(d, ast.Attribute) and d.attr == name:
            return True, None
    return False, None


def has_kw_true(call: ast.Call | None, key: str) -> bool:
    if call is None:
        return False
    for kw in call.keywords:
        if kw.arg == key and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def is_float_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, float)


def ann_str(node: ast.expr | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "?"


def class_annotations(cls_node: ast.ClassDef) -> dict[str, ast.expr]:
    """Top-level `name: T` annotations in a class body."""
    out: dict[str, ast.expr] = {}
    for stmt in cls_node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out[stmt.target.id] = stmt.annotation
    return out


def class_method_names(cls_node: ast.ClassDef) -> set[str]:
    return {
        s.name
        for s in cls_node.body
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


# ── Rule: layer import (ADR-002) ──────────────────────────────────────────


def check_imports(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    external_forbid = {
        "domain": DOMAIN_FORBIDDEN_EXTERNAL,
        "application": APPLICATION_FORBIDDEN_EXTERNAL,
        "infrastructure": INFRASTRUCTURE_FORBIDDEN_EXTERNAL,
    }.get(layer, frozenset())
    internal_forbid = INTERNAL_FORBIDDEN.get(layer, frozenset())
    rel = rel_within_layer(path, layer) if layer != "tests" else ""

    for lineno, dotted in iter_imports(tree):
        pkg = top_pkg(dotted)
        if pkg in external_forbid:
            if (layer, rel, pkg) in EXTERNAL_EXCEPTIONS:
                continue
            report.add(Violation(
                file=file_rel(path), line=lineno,
                rule="ADR-002/external-import",
                message=f"{layer}/ must not import `{dotted}` (forbidden in this layer)",
            ))
        if dotted.startswith(f"{PKG}."):
            hit = matches_internal_forbid(dotted, internal_forbid)
            if hit:
                report.add(Violation(
                    file=file_rel(path), line=lineno,
                    rule="ADR-002/cross-layer",
                    message=f"{layer}/ must not import `{dotted}` (depends on forbidden layer `{hit}`)",
                ))


# ── Rule: monetary field typing (ADR-003) ─────────────────────────────────


def check_money_typing(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer not in ("domain", "application"):
        return
    for node in ast.walk(tree):
        ann: ast.expr | None = None
        name = ""
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            ann = node.annotation
            name = node.target.id
        elif isinstance(node, ast.arg) and node.annotation is not None:
            ann = node.annotation
            name = node.arg
        else:
            continue
        lname = name.lower()
        if lname not in EXACT_MONEY_NAMES and not lname.endswith(MONEY_SUFFIXES):
            continue
        src = ann_str(ann)
        if "Money" in src or "Decimal" in src:
            continue
        if "float" in src:
            report.add(Violation(
                file=file_rel(path), line=ann.lineno,
                rule="ADR-003/money-as-float",
                message=f"`{name}: {src}` — monetary field must be Money/Decimal, never float",
            ))
        else:
            report.add(Violation(
                file=file_rel(path), line=ann.lineno,
                rule="ADR-003/suspect-money-name",
                message=f"`{name}: {src}` looks monetary but is not Money/Decimal",
            ))


# ── Rule: Money/Decimal constructed from float literal (ADR-003) ──────────


def check_decimal_money_from_float(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer is None or layer == "tests":
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            fname = f.id
        elif isinstance(f, ast.Attribute):
            fname = f.attr
        else:
            continue
        if fname not in ("Decimal", "Money"):
            continue
        if not node.args:
            continue
        if is_float_literal(node.args[0]):
            rule = "ADR-003/decimal-from-float" if fname == "Decimal" else "ADR-003/money-from-float"
            report.add(Violation(
                file=file_rel(path), line=node.lineno,
                rule=rule,
                message=f"{fname}({node.args[0].value!r}, ...) — money must be built from str/int, not float",
            ))


# ── Rule: Repository Protocol must live in domain/ ────────────────────────


def check_repo_protocol_location(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer == "tests":
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.endswith("Repository"):
            continue
        if not class_has_base(node, "Protocol"):
            continue
        if layer != "domain":
            report.add(Violation(
                file=file_rel(path), line=node.lineno,
                rule="ADR-002/repo-protocol-location",
                message=(
                    f"Repository Protocol `{node.name}` must live under `domain/`, "
                    f"found in `{layer}/`"
                ),
            ))


# ── Rule: ORM Row classes must live in infrastructure/db/models.py ────────


def check_orm_row_location(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer == "tests":
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.endswith("Row"):
            continue
        if not class_has_base(node, "Base"):
            continue
        expected = SRC / "infrastructure" / "db" / "models.py"
        if path != expected:
            report.add(Violation(
                file=file_rel(path), line=node.lineno,
                rule="ADR-002/orm-row-location",
                message=(
                    f"ORM Row `{node.name}` must live in `infrastructure/db/models.py`, "
                    f"found in `{file_rel(path)}`"
                ),
            ))


# ── Rule: Pydantic models must live under interfaces/ ─────────────────────


def check_pydantic_location(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer in (None, "tests", "interfaces"):
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not class_has_base(node, "BaseModel"):
            continue
        report.add(Violation(
            file=file_rel(path), line=node.lineno,
            rule="ADR-002/pydantic-location",
            message=(
                f"Pydantic model `{node.name}` must live under `interfaces/`, "
                f"found in `{layer}/`"
            ),
        ))


# ── Rule: Domain events must be frozen+slots+kw_only dataclasses ──────────


def check_event_shape(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer not in ("domain",):
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # Match either `DomainEvent` itself OR any subclass of it.
        is_base_event = node.name == "DomainEvent"
        if not (is_base_event or class_has_base(node, "DomainEvent")):
            continue
        present, call = find_decorator(node, "dataclass")
        if not present:
            report.add(Violation(
                file=file_rel(path), line=node.lineno,
                rule="ADR-006/event-shape",
                message=f"`{node.name}` must be `@dataclass(frozen=True, slots=True, kw_only=True)`",
            ))
            continue
        missing = [k for k in ("frozen", "slots", "kw_only") if not has_kw_true(call, k)]
        if missing:
            report.add(Violation(
                file=file_rel(path), line=node.lineno,
                rule="ADR-006/event-shape",
                message=(
                    f"`{node.name}` `@dataclass(...)` missing kw={missing} "
                    f"(required: frozen=True, slots=True, kw_only=True)"
                ),
            ))


# ── Rule: Domain events must declare aggregate_id field ───────────────────


def check_event_has_aggregate_id(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer != "domain":
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # DomainEvent base itself only carries `metadata`; subclasses must add aggregate_id.
        if node.name == "DomainEvent":
            continue
        if not class_has_base(node, "DomainEvent"):
            continue
        if "aggregate_id" not in class_annotations(node):
            report.add(Violation(
                file=file_rel(path), line=node.lineno,
                rule="ADR-006/event-aggregate-id",
                message=f"DomainEvent `{node.name}` must declare an `aggregate_id` field",
            ))


# ── Rule: Aggregates must NOT be @dataclass (mutability + pull_events) ────


def check_aggregate_not_dataclass(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer != "domain":
        return
    if path.name != "entity.py":
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # Heuristic: a class declaring `_pending_events` or `pull_events()` is an aggregate.
        has_pending = "_pending_events" in class_annotations(node) or any(
            isinstance(s, ast.Assign) and any(
                isinstance(t, ast.Attribute) and t.attr == "_pending_events"
                for t in s.targets
            )
            for s in ast.walk(node)
        )
        has_pull = "pull_events" in class_method_names(node)
        if not (has_pending or has_pull):
            continue
        present, _ = find_decorator(node, "dataclass")
        if present:
            report.add(Violation(
                file=file_rel(path), line=node.lineno,
                rule="ADR-006/aggregate-not-dataclass",
                message=(
                    f"Aggregate `{node.name}` must NOT be `@dataclass` — mutation of "
                    f"`_pending_events` requires a plain class"
                ),
            ))


# ── Rule: Domain entities must NOT carry created_at/updated_at (ADR-007) ─


def check_no_audit_on_entity(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer != "domain":
        return
    if path.name != "entity.py":
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        anns = class_annotations(node)
        for forbidden in ("created_at", "updated_at"):
            if forbidden in anns:
                report.add(Violation(
                    file=file_rel(path), line=anns[forbidden].lineno,
                    rule="ADR-007/no-audit-on-entity",
                    message=(
                        f"Domain entity `{node.name}` must not declare `{forbidden}` "
                        f"(ADR-007: audit on ORM row, not domain)"
                    ),
                ))


# ── Rule: datetime.now() must take tz argument ───────────────────────────


def check_datetime_tz(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer is None:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # match `datetime.now(...)` and `datetime.utcnow(...)`
        if isinstance(f, ast.Attribute):
            if f.attr == "utcnow":
                report.add(Violation(
                    file=file_rel(path), line=node.lineno,
                    rule="ADR-007/datetime-tz-required",
                    message="datetime.utcnow() is forbidden — use datetime.now(UTC)",
                ))
                continue
            if f.attr != "now":
                continue
            # restrict to datetime.now (not Foo.now)
            base = f.value
            base_name = base.id if isinstance(base, ast.Name) else (
                base.attr if isinstance(base, ast.Attribute) else None
            )
            if base_name != "datetime":
                continue
            if not node.args and not node.keywords:
                report.add(Violation(
                    file=file_rel(path), line=node.lineno,
                    rule="ADR-007/datetime-tz-required",
                    message="datetime.now() must take a tz argument (e.g. datetime.now(UTC))",
                ))


# ── Rule: no `assert` in application/ or infrastructure/ ──────────────────


def check_no_assert_runtime(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer not in ("application", "infrastructure"):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            report.add(Violation(
                file=file_rel(path), line=node.lineno,
                rule="ADR-002/assert-runtime",
                message=(
                    "`assert` is forbidden in application/ and infrastructure/ — "
                    "asserts disappear under python -O. Raise an explicit error."
                ),
            ))


# ── Rule: UC class shape — @dataclass with uow: UnitOfWork ────────────────


def _injects_repository(node: ast.ClassDef) -> bool:
    """True when a UC takes a `*Repository` via an explicit `__init__`.

    Repo-pattern UCs (e.g. the `system/jobs` operational aggregates from
    ADR-034) manage a single aggregate's persistence through its dedicated
    repository rather than the shared UnitOfWork — they run outside any HTTP
    request / multi-aggregate transaction (the `@log_run` decorator records
    runs from inside the Arq worker). The dataclass+uow shape doesn't apply.

    Detected structurally so the carve-out can't be abused by domain command
    UCs: those are `@dataclass` with `uow: UnitOfWork` and define no explicit
    `__init__` (the dataclass generates it), so they never match here.
    """
    init = next(
        (s for s in node.body if isinstance(s, ast.FunctionDef) and s.name == "__init__"),
        None,
    )
    if init is None:
        return False
    for arg in (*init.args.args, *init.args.kwonlyargs):
        if arg.annotation is not None and "Repository" in ann_str(arg.annotation):
            return True
    return False


def _is_readonly_query_uc(node: ast.ClassDef) -> bool:
    """ADR-035: a read-only query UC injects a *ReadModelPort / *QueryPort and
    never calls self.uow.commit() — it reads projections, holds no UnitOfWork."""
    anns = class_annotations(node)
    inits = [s for s in node.body if isinstance(s, ast.FunctionDef) and s.name == "__init__"]
    typed = list(anns.values()) + [
        a.annotation for fn in inits for a in (*fn.args.args, *fn.args.kwonlyargs)
        if a.annotation is not None
    ]
    injects_read = any(
        "ReadModelPort" in ann_str(t) or "QueryPort" in ann_str(t) for t in typed
    )
    if not injects_read:
        return False
    execute = next(
        (s for s in node.body if isinstance(s, ast.AsyncFunctionDef) and s.name == "execute"),
        None,
    )
    if execute is None:
        return False
    commits = any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "commit"
        for c in ast.walk(execute)
    )
    return not commits


def check_uc_shape(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer != "application":
        return
    # handler files don't define UC classes; skip
    if path.name == "handlers.py" or path.name.endswith("_handler.py"):
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # A UC is any class with an `async def execute(self, ...)` method.
        execute_meth = next(
            (s for s in node.body if isinstance(s, ast.AsyncFunctionDef) and s.name == "execute"),
            None,
        )
        if execute_meth is None:
            continue
        # Repo-pattern UCs (ADR-034 system/jobs) are exempt from dataclass+uow.
        # Read-only query UCs (ADR-035) inject *ReadModelPort/*QueryPort, no uow.
        if _injects_repository(node) or _is_readonly_query_uc(node):
            continue
        present, _ = find_decorator(node, "dataclass")
        if not present:
            report.add(Violation(
                file=file_rel(path), line=node.lineno,
                rule="UC/dataclass-with-uow",
                message=f"Use case `{node.name}` must be a `@dataclass`",
            ))
        anns = class_annotations(node)
        if "uow" not in anns:
            report.add(Violation(
                file=file_rel(path), line=node.lineno,
                rule="UC/dataclass-with-uow",
                message=f"Use case `{node.name}` must declare a `uow: UnitOfWork` field",
            ))
        else:
            t = ann_str(anns["uow"])
            if "UnitOfWork" not in t:
                report.add(Violation(
                    file=file_rel(path), line=anns["uow"].lineno,
                    rule="UC/dataclass-with-uow",
                    message=(
                        f"Use case `{node.name}` field `uow: {t}` must be typed `UnitOfWork`"
                    ),
                ))


# ── Rule: handlers.py registrar + signature ───────────────────────────────


def check_handler_signature(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer != "application":
        return
    if path.name != "handlers.py" and not path.name.endswith("_handler.py"):
        return
    has_registrar = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("register_"):
            has_registrar = True
            args = node.args.args
            if not args or not any(a.arg == "bus" for a in args):
                report.add(Violation(
                    file=file_rel(path), line=node.lineno,
                    rule="handler/registrar-signature",
                    message=(
                        f"Registrar `{node.name}` must take a `bus: EventBus` parameter"
                    ),
                ))
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("_on_"):
            args = [a.arg for a in node.args.args]
            if args != ["event", "uow"]:
                report.add(Violation(
                    file=file_rel(path), line=node.lineno,
                    rule="handler/signature",
                    message=(
                        f"Handler `{node.name}` must be `async def _on_*(event, uow)` "
                        f"— got positional args {args}"
                    ),
                ))
    if not has_registrar:
        report.add(Violation(
            file=file_rel(path), line=1,
            rule="handler/registrar-missing",
            message=(
                f"`{path.name}` must define at least one `register_*(bus: EventBus)` function"
            ),
        ))


# ── Rule: HTTP routers must use Annotated[..., Depends] ───────────────────


def _is_routers_file(path: Path) -> bool:
    try:
        rel = path.relative_to(SRC / "interfaces" / "http" / "routers")
    except ValueError:
        return False
    return (rel.parts and rel.suffix == ".py") or True


def check_http_router(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer != "interfaces":
        return
    if not _is_routers_file(path):
        return

    # Annotated-Depends check
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        all_args = list(node.args.args) + list(node.args.kwonlyargs)
        defaults = list(node.args.defaults) + list(node.args.kw_defaults)
        # align defaults to args (positionals tail-align; kwonly are 1:1)
        pos = node.args.args
        pos_defaults: list[ast.expr | None] = [None] * (len(pos) - len(node.args.defaults))
        pos_defaults += list(node.args.defaults)
        all_pairs = list(zip(pos, pos_defaults, strict=False))
        all_pairs += list(zip(node.args.kwonlyargs, node.args.kw_defaults, strict=False))
        for arg, default in all_pairs:
            if default is None or not isinstance(default, ast.Call):
                continue
            fname = default.func.id if isinstance(default.func, ast.Name) else (
                default.func.attr if isinstance(default.func, ast.Attribute) else None
            )
            if fname == "Depends":
                report.add(Violation(
                    file=file_rel(path), line=arg.lineno,
                    rule="http/annotated-depends",
                    message=(
                        f"`{arg.arg}: ... = Depends(...)` must use "
                        f"`Annotated[<T>, Depends(...)]` instead"
                    ),
                ))
        _ = all_args, defaults

    # No direct session imports
    for lineno, module, names in iter_from_imports(tree):
        if module.endswith(".db.session") or module.endswith(".infrastructure.db.session"):
            for n in names:
                if n in {"session_factory", "get_session", "_session_factory"}:
                    report.add(Violation(
                        file=file_rel(path), line=lineno,
                        rule="http/no-session-import",
                        message=(
                            f"Router must not import `{n}` from session module — "
                            f"go through `Depends(get_uow)`"
                        ),
                    ))


# ── Rule: tests must not use pytest.skip() ────────────────────────────────


def check_pytest_skip(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer != "tests":
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "skip":
            base = f.value
            if isinstance(base, ast.Name) and base.id == "pytest":
                report.add(Violation(
                    file=file_rel(path), line=node.lineno,
                    rule="tests/pytest-skip-direct",
                    message=(
                        "`pytest.skip(...)` is forbidden — use `@pytest.mark.skipif(..., reason=...)`"
                    ),
                ))


# ── Rule F-05: aggregate __init__ must not call event-emitting methods ───


def _appends_to_pending_events(node: ast.AST) -> bool:
    """Heuristic: does this method body contain `self._pending_events.append(...)`?"""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        if not isinstance(f, ast.Attribute) or f.attr != "append":
            continue
        # f.value should be `self._pending_events`
        target = f.value
        if isinstance(target, ast.Attribute) and target.attr == "_pending_events":
            base = target.value
            if isinstance(base, ast.Name) and base.id == "self":
                return True
    return False


def check_aggregate_init_no_event_leak(
    path: Path, tree: ast.AST, layer: str, report: Report
) -> None:
    if layer != "domain" or path.name != "entity.py":
        return
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        # Map method-name → emits_event?
        emitters: set[str] = set()
        init_def: ast.FunctionDef | None = None
        for s in cls.body:
            if isinstance(s, ast.FunctionDef):
                if _appends_to_pending_events(s):
                    emitters.add(s.name)
                if s.name == "__init__":
                    init_def = s
        if init_def is None or not emitters:
            continue
        for sub in ast.walk(init_def):
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            if not isinstance(f, ast.Attribute):
                continue
            base = f.value
            if isinstance(base, ast.Name) and base.id == "self" and f.attr in emitters:
                report.add(Violation(
                    file=file_rel(path), line=sub.lineno,
                    rule="ADR-006/init-event-leak",
                    message=(
                        f"`{cls.name}.__init__` calls `self.{f.attr}()` which "
                        f"emits an event — re-hydration from the DB would re-emit. "
                        f"Inline the state mutation directly."
                    ),
                ))


# ── Rule F-07: events are data carriers only (no business methods) ──────


def check_event_no_methods(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer != "domain":
        return
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        if cls.name == "DomainEvent":
            continue
        if not class_has_base(cls, "DomainEvent"):
            continue
        for s in cls.body:
            if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Allow dunder methods + plain `__post_init__` for dataclass hooks
            if s.name.startswith("__") and s.name.endswith("__"):
                continue
            report.add(Violation(
                file=file_rel(path), line=s.lineno,
                rule="ADR-006/event-no-methods",
                message=(
                    f"DomainEvent `{cls.name}` declares method `{s.name}` — "
                    f"events are immutable data carriers; move logic onto the "
                    f"aggregate or a domain service."
                ),
            ))


# ── Rule F-10: unit tests must not import `session_factory` directly ────


_FORBIDDEN_SESSION_IMPORTS = frozenset({"session_factory", "_session_factory"})


def check_test_no_session_import(
    path: Path, tree: ast.AST, layer: str, report: Report
) -> None:
    if layer != "tests":
        return
    # conftest.py is allowed to wire fixtures
    if path.name == "conftest.py":
        return
    for lineno, module, names in iter_from_imports(tree):
        if not module.endswith(".db.session"):
            continue
        for n in names:
            if n in _FORBIDDEN_SESSION_IMPORTS:
                report.add(Violation(
                    file=file_rel(path), line=lineno,
                    rule="tests/no-session-import",
                    message=(
                        f"Tests must not import `{n}` directly — use the "
                        f"`uow`/`session` fixture from conftest.py."
                    ),
                ))


# ── Rule R-02: aggregate must have `_pending_events` + `pull_events()` ──


def check_aggregate_event_api(
    path: Path, tree: ast.AST, layer: str, report: Report
) -> None:
    if layer != "domain" or path.name != "entity.py":
        return
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        # Identify the aggregate by the presence of pull_events OR _pending_events;
        # we want to flag the case where one exists and the other doesn't.
        methods = class_method_names(cls)
        has_pull = "pull_events" in methods
        # _pending_events is typically assigned in __init__, not class-body annotated.
        has_pending = False
        for sub in ast.walk(cls):
            if isinstance(sub, ast.Attribute) and sub.attr == "_pending_events":
                if isinstance(sub.value, ast.Name) and sub.value.id == "self":
                    has_pending = True
                    break
        # If neither, this isn't an aggregate (probably a helper class). Skip.
        if not (has_pull or has_pending):
            continue
        if not has_pending:
            report.add(Violation(
                file=file_rel(path), line=cls.lineno,
                rule="ADR-006/aggregate-pending-events",
                message=(
                    f"Aggregate `{cls.name}` has `pull_events()` but no "
                    f"`self._pending_events` list to back it."
                ),
            ))
        if not has_pull:
            report.add(Violation(
                file=file_rel(path), line=cls.lineno,
                rule="ADR-006/aggregate-pull-events",
                message=(
                    f"Aggregate `{cls.name}` references `_pending_events` but "
                    f"does not expose `pull_events()` — the UoW dispatches via "
                    f"this method."
                ),
            ))


# ── Rule R-05: UC.execute must call uow.commit() ─────────────────────────


def check_uc_commits(path: Path, tree: ast.AST, layer: str, report: Report) -> None:
    if layer != "application":
        return
    if path.name == "handlers.py" or path.name.endswith("_handler.py"):
        return
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        execute = next(
            (s for s in cls.body if isinstance(s, ast.AsyncFunctionDef) and s.name == "execute"),
            None,
        )
        if execute is None:
            continue
        commits = False
        for sub in ast.walk(execute):
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            if isinstance(f, ast.Attribute) and f.attr == "commit":
                base = f.value
                # match `self.uow.commit()`
                if (
                    isinstance(base, ast.Attribute)
                    and base.attr == "uow"
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "self"
                ):
                    commits = True
                    break
        # Skeleton UCs that just `raise NotImplementedError` — exempt.
        is_skeleton = any(
            isinstance(s, ast.Raise)
            and isinstance(s.exc, ast.Call)
            and isinstance(s.exc.func, ast.Name)
            and s.exc.func.id == "NotImplementedError"
            for s in ast.walk(execute)
        )
        # QUERY UCs (read-only) — exempt. Detected structurally: the body
        # never calls `self.uow.collect(...)` nor any `<repo>.save(...)`.
        # Pure reads have no atomic boundary to commit.
        mutates = False
        for sub in ast.walk(execute):
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            if isinstance(f, ast.Attribute) and f.attr in ("collect", "save"):
                mutates = True
                break
        is_query = not mutates
        if not commits and not is_skeleton and not is_query:
            report.add(Violation(
                file=file_rel(path), line=execute.lineno,
                rule="UC/commit-required",
                message=(
                    f"`{cls.name}.execute()` must call `await self.uow.commit()` "
                    f"somewhere in its body (atomic boundary per ADR-002)."
                ),
            ))


# ── Rule R-12: tests with `await` must be `async def` ────────────────────


def check_test_async_for_await(
    path: Path, tree: ast.AST, layer: str, report: Report
) -> None:
    if layer != "tests":
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue
        # search for Await inside without crossing into nested AsyncFunctionDef
        for sub in ast.walk(node):
            if isinstance(sub, ast.Await):
                report.add(Violation(
                    file=file_rel(path), line=node.lineno,
                    rule="tests/sync-with-await",
                    message=(
                        f"`def {node.name}` contains `await` — must be "
                        f"`async def` (pytest-asyncio auto-mode picks it up)."
                    ),
                ))
                break


# ── Driver ─────────────────────────────────────────────────────────────────


ALL_CHECKS = (
    check_imports,
    check_money_typing,
    check_decimal_money_from_float,
    check_repo_protocol_location,
    check_orm_row_location,
    check_pydantic_location,
    check_event_shape,
    check_event_has_aggregate_id,
    check_event_no_methods,
    check_aggregate_not_dataclass,
    check_aggregate_init_no_event_leak,
    check_aggregate_event_api,
    check_no_audit_on_entity,
    check_datetime_tz,
    check_no_assert_runtime,
    check_uc_shape,
    check_uc_commits,
    check_handler_signature,
    check_http_router,
    check_pytest_skip,
    check_test_no_session_import,
    check_test_async_for_await,
)


def collect_python_files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        p = p.resolve()
        if p.is_file() and p.suffix == ".py":
            out.append(p)
        elif p.is_dir():
            out.extend(
                f for f in p.rglob("*.py") if "__pycache__" not in f.parts
            )
    return sorted(set(out))


def run(paths: list[Path]) -> Report:
    report = Report()
    for f in collect_python_files(paths):
        layer = layer_of(f)
        if layer is None:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError as e:
            report.add(Violation(
                file=file_rel(f), line=e.lineno or 0,
                rule="parse-error", message=f"SyntaxError: {e.msg}",
            ))
            continue
        for check in ALL_CHECKS:
            check(f, tree, layer, report)
    return report


def format_text(report: Report) -> str:
    if report.ok:
        return f"✅ Gate 2.5 PASS — 0 architectural violations ({len(ALL_CHECKS)} rules enforced).\n"
    by_rule: dict[str, list[Violation]] = {}
    for v in report.violations:
        by_rule.setdefault(v.rule, []).append(v)
    lines = [f"⛔ Gate 2.5 FAIL — {len(report.violations)} violation(s):", ""]
    for rule in sorted(by_rule):
        lines.append(f"  [{rule}]  ({len(by_rule[rule])})")
        for v in by_rule[rule]:
            lines.append(f"    {v.file}:{v.line}  {v.message}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 2.5 architectural lint.")
    parser.add_argument(
        "--paths", nargs="*", default=None,
        help="Paths to scan (default: src/mywb).",
    )
    parser.add_argument(
        "paths_positional", nargs="*",
        help="Paths to scan (positional form, same as --paths).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args()

    explicit = (args.paths or []) + (args.paths_positional or [])
    targets = [Path(p) for p in explicit] if explicit else [SRC]
    report = run(targets)

    if args.json:
        print(json.dumps(
            {"ok": report.ok, "violations": [v.__dict__ for v in report.violations]},
            indent=2,
        ))
    else:
        print(format_text(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
