#!/usr/bin/env python3
"""Release report generator (ADR-077).

Produces a self-contained HTML page summarising ONE deployment: the cumulative
artifact inventory of the repo (ADRs / specs / lessons / …) plus this release's
gate / test / CI-CD / code-review / code-scan results for a given commit SHA.

Design goals:
  - pure stdlib; shells out to `git` and (optionally) `gh` only.
  - GRACEFUL DEGRADATION: with no `gh` / no network it still emits the cumulative
    inventory + the git-derived bits (commit, REVIEW-VERDICT trailer), marking the
    Actions-derived sections as "not collected" rather than failing. So it runs
    both in CI (rich) and on a laptop (degraded) and always produces a page.
  - IDEMPOTENT: writing the same SHA overwrites; the index is rebuilt from the
    files on disk, so re-runs converge.

Modes:
  generate --sha <sha> [--repo owner/name] [--out-dir deploy/reports]
                                  write <sha>.html + rebuild index.html; print path
  check    --sha <sha> [--out-dir deploy/reports]
                                  exit 0 iff <sha>.html exists (enforcement hook)
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    ).stdout.strip()
    or "."
)

# ─────────────────────────────── shell helpers ──────────────────────────────


def _run(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    p = subprocess.run(args, capture_output=True, text=True, cwd=cwd or ROOT)
    return p.returncode, (p.stdout or "")


def _git(*args: str) -> str:
    return _run(["git", *args])[1].strip()


def _gh_json(path: str):
    """Fetch `GET /<path>` from the GitHub API as parsed JSON, or None on any
    failure (degraded mode). Uses `gh api` when the CLI is present (laptop), else
    falls back to `curl` with GH_TOKEN/GITHUB_TOKEN (CI containers ship curl but
    not gh — keeps the generator dependency-light + portable)."""
    if shutil.which("gh"):
        rc, out = _run(["gh", "api", "-H", "Accept: application/vnd.github+json", path])
    else:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not (token and shutil.which("curl")):
            return None
        rc, out = _run([
            "curl", "-fsSL",
            "-H", "Accept: application/vnd.github+json",
            "-H", f"Authorization: Bearer {token}",
            "-H", "X-GitHub-Api-Version: 2022-11-28",
            f"https://api.github.com/{path.lstrip('/')}",
        ])
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# ─────────────────────────────── collectors ─────────────────────────────────


def _count(glob: str, *, under: str, exclude: tuple[str, ...] = ()) -> int:
    base = ROOT / under
    if not base.exists():
        return 0
    n = 0
    for p in base.rglob(glob):
        if any(x in p.name for x in exclude):
            continue
        n += 1
    return n


def collect_inventory() -> list[dict]:
    """Cumulative repo artifacts — counted from the working tree at this SHA.

    KIT NOTE: the .dev/{adr,lessons,specs} rows are generic (the kit's
    conventions). The `bounded contexts` (domain/) and `DB migrations`
    (alembic) rows are project-specific — edit or drop them for your layout;
    they harmlessly report 0 when the paths are absent."""
    adr = len([p for p in (ROOT / ".dev/adr").glob("ADR-*.md")]) if (ROOT / ".dev/adr").exists() else 0
    lessons = _count("*.md", under=".dev/lessons", exclude=("README",))
    entity_specs = _count("*-spec.md", under=".dev/specs")
    uc_yaml = len([p for p in (ROOT / ".dev/specs").rglob("*.yaml") if p.parent.name == "usecase"]) if (ROOT / ".dev/specs").exists() else 0
    migrations = _count("*.py", under="backend/alembic/versions", exclude=("__init__",))
    contexts = 0
    dom = ROOT / "backend/src/mywb/domain"
    if dom.exists():
        contexts = len([d for d in dom.iterdir() if d.is_dir() and not d.name.startswith("_") and d.name != "shared"])
    return [
        {"label": "ADRs", "n": adr, "hint": "architecture decision records — .dev/adr/"},
        {"label": "Entity specs", "n": entity_specs, "hint": "aggregate behavioural specs — .dev/specs/*/*/*-spec.md"},
        {"label": "Use-case specs", "n": uc_yaml, "hint": "one YAML per use case — .dev/specs/*/*/usecase/"},
        {"label": "Lessons learnt", "n": lessons, "hint": "learned-the-hard-way notes — .dev/lessons/"},
        {"label": "Bounded contexts", "n": contexts, "hint": "DDD subpackages — domain/"},
        {"label": "DB migrations", "n": migrations, "hint": "Alembic versions"},
    ]


def collect_commit(sha: str) -> dict:
    subject = _git("log", "-1", "--format=%s", sha) or "(unknown)"
    author = _git("log", "-1", "--format=%an", sha)
    date = _git("log", "-1", "--format=%cI", sha)
    # Prefer git's native trailer parser (clean case). It only fires when the
    # trailer sits in the message's final contiguous block — but a GitHub squash
    # can isolate REVIEW-WAIVED with blank lines so it isn't recognised. Fall back
    # to scanning the whole message for the LAST `REVIEW-(VERDICT|WAIVED): …` line:
    # a real trailer always comes after any prose that merely mentions the token.
    passed = _git("log", "-1", "--format=%(trailers:key=REVIEW-VERDICT,valueonly)", sha).strip()
    waived = _git("log", "-1", "--format=%(trailers:key=REVIEW-WAIVED,valueonly)", sha).strip()
    if not passed and not waived:
        body = _git("log", "-1", "--format=%B", sha)
        for line in body.splitlines():
            m = re.match(r"\s*REVIEW-(VERDICT|WAIVED):\s*(.+?)\s*$", line)
            if m:
                if m.group(1) == "WAIVED":
                    waived, passed = m.group(2), ""
                else:
                    passed, waived = m.group(2), ""
    if waived:
        # keep it short — the reason can be a paragraph
        first = waived.splitlines()[0].strip()
        verdict = "waived — " + (first[:80] + "…" if len(first) > 80 else first)
    elif passed:
        verdict = passed.splitlines()[0].strip() or "none"
    else:
        verdict = "none"
    return {"sha": sha, "short": sha[:8], "subject": subject, "author": author, "date": date, "verdict": verdict}


_SCAN_WORKFLOW = "security"


def collect_actions(repo: str | None, sha: str) -> dict:
    """Query GitHub Actions runs for this SHA. Returns a dict with
    `available` False in degraded mode."""
    if not repo:
        return {"available": False, "reason": "no --repo given"}
    data = _gh_json(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100")
    if data is None:
        return {"available": False, "reason": "gh unavailable / unauthenticated"}
    runs = data.get("workflow_runs", [])
    if not runs:
        return {"available": True, "runs": [], "jobs_by_run": {}}
    # Pick one run per workflow name. Prefer a COMPLETED run over an in-flight
    # re-run (else a report generated while a re-run is queued shows "queued");
    # among same-completion, the higher run_number wins.
    def _better(cand: dict, cur: dict) -> bool:
        cand_done = cand.get("status") == "completed"
        cur_done = cur.get("status") == "completed"
        if cand_done != cur_done:
            return cand_done
        return cand.get("run_number", 0) > cur.get("run_number", 0)

    latest: dict[str, dict] = {}
    for r in runs:
        name = (r.get("name") or "").strip().lower()
        if name not in latest or _better(r, latest[name]):
            latest[name] = r
    jobs_by_run: dict[str, list] = {}
    for name, r in latest.items():
        jd = _gh_json(f"repos/{repo}/actions/runs/{r['id']}/jobs?per_page=100")
        jobs = (jd or {}).get("jobs", []) if jd else []
        jobs_by_run[name] = [
            {"name": j.get("name"), "conclusion": j.get("conclusion") or j.get("status")}
            for j in jobs
        ]
    return {"available": True, "runs": list(latest.values()), "jobs_by_run": jobs_by_run, "latest": latest}


# ─────────────────────────────── rendering ──────────────────────────────────

_CONCLUSION_PILL = {
    "success": ("pass", "✔ pass"),
    "failure": ("fail", "✘ fail"),
    "cancelled": ("warn", "cancelled"),
    "skipped": ("skip", "skipped"),
    "neutral": ("skip", "neutral"),
    "timed_out": ("fail", "timed out"),
    None: ("skip", "—"),
}


def _pill(conclusion: str | None) -> str:
    cls, txt = _CONCLUSION_PILL.get(conclusion, ("warn", html.escape(str(conclusion))))
    return f'<span class="pill {cls}">{txt}</span>'


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _section_jobs(actions: dict, workflow_names: set[str], title: str, empty_note: str) -> str:
    if not actions.get("available"):
        return (
            f'<h3>{_esc(title)}</h3><p class="muted">Not collected '
            f'({_esc(actions.get("reason", "degraded mode"))}). '
            f"Run in CI or with an authenticated <code>gh</code> to populate.</p>"
        )
    jbr = actions.get("jobs_by_run", {})
    rows = []
    for wf_name, jobs in jbr.items():
        if wf_name not in workflow_names:
            continue
        for j in jobs:
            rows.append(
                f"<tr><td>{_esc(wf_name)}</td><td>{_esc(j['name'])}</td>"
                f"<td>{_pill(j['conclusion'])}</td></tr>"
            )
    if not rows:
        return f'<h3>{_esc(title)}</h3><p class="muted">{_esc(empty_note)}</p>'
    return (
        f"<h3>{_esc(title)}</h3><table><tr><th>workflow</th><th>job</th>"
        f"<th>result</th></tr>{''.join(rows)}</table>"
    )


def render_html(sha: str, repo: str | None, generated_at: str, require_actions: bool = False) -> str:
    inv = collect_inventory()
    commit = collect_commit(sha)
    actions = collect_actions(repo, sha)
    if require_actions and not actions.get("available"):
        # Fail loud rather than ship an inventory-only page from a CI run whose
        # `gh` is missing/unauthenticated (repo convention: adapter selection
        # fails loud on a missing dependency).
        raise SystemExit(
            f"release-report: --require-actions set but Actions data unavailable "
            f"({actions.get('reason', 'unknown')}). Ensure `gh` (or curl + GH_TOKEN) "
            f"can reach the GitHub API and --repo is set."
        )

    # overall roll-up
    overall = "unknown"
    if actions.get("available") and actions.get("latest"):
        concs = [r.get("conclusion") for r in actions["latest"].values() if r.get("conclusion")]
        if concs and all(c in ("success", "skipped") for c in concs):
            overall = "success"
        elif any(c == "failure" for c in concs):
            overall = "failure"
        else:
            overall = "mixed"

    inv_cards = "".join(
        f'<div class="stat"><div class="n">{i["n"]}</div><div class="l">{_esc(i["label"])}</div>'
        f'<div class="h">{_esc(i["hint"])}</div></div>'
        for i in inv
    )

    verdict = commit["verdict"]
    v_cls = "pass" if verdict == "pass" else ("warn" if verdict.startswith("waived") else "fail")

    checks_tbl = _section_jobs(actions, {"checks"}, "Gates · tests · code review (checks workflow)", "no checks run found for this SHA")
    scan_tbl = _section_jobs(actions, {_SCAN_WORKFLOW}, "Code scan (security workflow)", "no security run found for this SHA")

    # CI/CD workflow-level roll-up
    cicd_rows = ""
    if actions.get("available") and actions.get("latest"):
        for name, r in sorted(actions["latest"].items()):
            cicd_rows += (
                f"<tr><td>{_esc(name)}</td>"
                f"<td>{_pill(r.get('conclusion'))}</td>"
                f'<td><a href="{_esc(r.get("html_url", "#"))}">run #{_esc(r.get("run_number"))}</a></td></tr>'
            )
        cicd = f"<table><tr><th>workflow</th><th>result</th><th>run</th></tr>{cicd_rows}</table>"
    else:
        cicd = f'<p class="muted">Not collected ({_esc(actions.get("reason", "degraded"))}).</p>'

    overall_pill = _pill("success" if overall == "success" else ("failure" if overall == "failure" else None))

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Release report · {_esc(commit['short'])}</title>
<style>
:root{{--bg:#0d0b08;--panel:#17130d;--line:#2a2118;--ink:#efe7d8;--muted:#9b8f7a;
--brass:#c9a24a;--brass-soft:#e0c88a;--sage:#5e9e7e;--oxblood:#b3564a;--slate:#7e93c8}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font-family:"Hanken Grotesk",system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
font-feature-settings:"tnum";line-height:1.5}}
.wrap{{max-width:1040px;margin:0 auto;padding:2.4rem 1.4rem 4rem}}
.eyebrow{{color:var(--brass);letter-spacing:.14em;text-transform:uppercase;font-size:.72rem}}
h1{{font-family:Newsreader,Georgia,serif;font-weight:600;font-size:2.1rem;margin:.2rem 0 .3rem}}
h2{{font-family:Newsreader,Georgia,serif;font-weight:600;font-size:1.35rem;margin:2.2rem 0 .8rem;
border-bottom:1px solid var(--line);padding-bottom:.4rem}}
h3{{font-size:1rem;color:#fff;margin:1.2rem 0 .5rem}}
.sub{{color:var(--muted);margin:.2rem 0 1rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.8rem}}
.stat{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:1rem}}
.stat .n{{font-family:Newsreader,serif;font-size:2rem;color:var(--brass-soft)}}
.stat .l{{font-weight:600;margin-top:.1rem}}
.stat .h{{color:var(--muted);font-size:.76rem;margin-top:.3rem}}
table{{width:100%;border-collapse:collapse;margin:.4rem 0;font-size:.9rem}}
th,td{{text-align:left;padding:.5rem .6rem;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--muted);font-weight:600;font-size:.76rem;text-transform:uppercase;letter-spacing:.06em}}
a{{color:var(--slate)}}
code{{background:#000;padding:.05rem .35rem;border-radius:5px;font-size:.85em;color:var(--brass-soft)}}
.muted{{color:var(--muted)}}
.pill{{display:inline-block;padding:.1rem .55rem;border-radius:999px;font-size:.76rem;font-weight:600}}
.pill.pass{{background:rgba(94,158,126,.18);color:#8fd3af;border:1px solid rgba(94,158,126,.5)}}
.pill.fail{{background:rgba(179,86,74,.18);color:#e79a90;border:1px solid rgba(179,86,74,.5)}}
.pill.warn{{background:rgba(201,162,74,.18);color:var(--brass-soft);border:1px solid rgba(201,162,74,.5)}}
.pill.skip{{background:#1e1810;color:var(--muted);border:1px solid var(--line)}}
.hero{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:1.4rem;margin-top:1rem}}
.hero .row{{display:flex;flex-wrap:wrap;gap:1.4rem;margin-top:.6rem}}
.hero .kv .k{{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.06em}}
.hero .kv .v{{font-size:.95rem}}
.foot{{color:var(--muted);font-size:.8rem;margin-top:2.4rem;border-top:1px solid var(--line);padding-top:1rem}}
</style></head><body><div class="wrap">
<div class="eyebrow">MyWB · release report</div>
<h1>Deployment summary — <code>{_esc(commit['short'])}</code></h1>
<p class="sub">One page per deployed commit: what the repo now contains, and how this release cleared every gate. Generated by <code>deploy/scripts/gen_release_report.py</code> (ADR-077).</p>

<div class="hero">
  <div style="display:flex;align-items:center;gap:.8rem;flex-wrap:wrap">
    <span style="font-size:1.05rem">Overall pipeline</span> {overall_pill}
    <span style="font-size:1.05rem;margin-left:1rem">Code review</span>
    <span class="pill {v_cls}">{_esc(verdict)}</span>
  </div>
  <div class="row">
    <div class="kv"><div class="k">commit</div><div class="v"><code>{_esc(commit['short'])}</code></div></div>
    <div class="kv"><div class="k">subject</div><div class="v">{_esc(commit['subject'])}</div></div>
    <div class="kv"><div class="k">author</div><div class="v">{_esc(commit['author'])}</div></div>
    <div class="kv"><div class="k">committed</div><div class="v">{_esc(commit['date'])}</div></div>
  </div>
</div>

<h2>Cumulative inventory <span class="muted" style="font-size:.8rem;font-weight:400">— what the codebase records, as of this commit</span></h2>
<div class="grid">{inv_cards}</div>

<h2>This release · CI/CD</h2>
{cicd}

<h2>This release · gates · tests · code review</h2>
{checks_tbl}

<h2>This release · code scan</h2>
{scan_tbl}

<div class="foot">
  Generated {_esc(generated_at)} · repo {_esc(repo or "(local)")} · SHA <code>{_esc(sha)}</code>.<br>
  Sections marked "not collected" mean GitHub Actions data was unavailable at generation time (degraded/local run). In CI the generator runs on the self-hosted runner with an authenticated <code>gh</code>, so the CI/scan/test tables are populated. See ADR-077.
</div>
</div></body></html>"""


def _rebuild_index(out_dir: Path) -> None:
    rows = []
    for p in sorted(out_dir.glob("*.html")):
        if p.name == "index.html":
            continue
        sha = p.stem
        subject = _git("log", "-1", "--format=%s", sha) or ""
        date = _git("log", "-1", "--format=%cI", sha) or ""
        rows.append(
            f'<tr><td><a href="{_esc(p.name)}"><code>{_esc(sha[:8])}</code></a></td>'
            f"<td>{_esc(date)}</td><td>{_esc(subject)}</td></tr>"
        )
    body = "".join(rows) or '<tr><td colspan="3" class="muted">No reports yet.</td></tr>'
    (out_dir / "index.html").write_text(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MyWB release reports</title><style>
body{{margin:0;background:#0d0b08;color:#efe7d8;font-family:"Hanken Grotesk",system-ui,sans-serif;
font-feature-settings:"tnum"}}.wrap{{max-width:960px;margin:0 auto;padding:2.4rem 1.4rem}}
h1{{font-family:Newsreader,Georgia,serif}}table{{width:100%;border-collapse:collapse;font-size:.92rem}}
th,td{{text-align:left;padding:.55rem .6rem;border-bottom:1px solid #2a2118}}
th{{color:#9b8f7a;font-size:.74rem;text-transform:uppercase;letter-spacing:.06em}}
a{{color:#7e93c8}}code{{background:#000;padding:.05rem .35rem;border-radius:5px;color:#e0c88a}}
.muted{{color:#9b8f7a}}</style></head><body><div class="wrap">
<h1>MyWB · release reports</h1>
<p class="muted">One report per deployed commit (ADR-077). Newest last.</p>
<table><tr><th>commit</th><th>committed</th><th>subject</th></tr>{body}</table>
</div></body></html>""",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["generate", "check"])
    ap.add_argument("--sha", default="HEAD")
    ap.add_argument("--repo", default=None, help="owner/name for GitHub Actions lookup")
    ap.add_argument("--out-dir", default="deploy/reports")
    ap.add_argument("--generated-at", default=None, help="ISO timestamp (CI passes one; default now)")
    ap.add_argument("--require-actions", action="store_true", help="fail if the GitHub Actions API is unavailable (CI use — don't ship a degraded page)")
    a = ap.parse_args()

    sha = _git("rev-parse", a.sha) or a.sha
    out_dir = (ROOT / a.out_dir).resolve()

    if a.mode == "check":
        exists = (out_dir / f"{sha}.html").exists()
        if not exists:
            print(f"🚫 release-report: no report for {sha[:8]} in {a.out_dir}", file=sys.stderr)
            return 1
        print(f"release-report: {sha[:8]}.html present — OK")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = a.generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    (out_dir / f"{sha}.html").write_text(render_html(sha, a.repo, generated_at, a.require_actions), encoding="utf-8")
    _rebuild_index(out_dir)
    print(str(out_dir / f"{sha}.html"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
