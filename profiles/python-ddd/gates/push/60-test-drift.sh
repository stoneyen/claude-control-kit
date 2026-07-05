#!/usr/bin/env bash
# every behavioural source change must carry a matching test change (check_test_drift.py).
# NOTE: the source<->test regexes inside the script ARE this project's map — adapt them.
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel)"
[ -f "$ROOT/.dev/gates/profile.env" ] && . "$ROOT/.dev/gates/profile.env"
: "${CCK_PY_ROOT:=backend}"; : "${CCK_PY:=uv run python}"
( cd "$ROOT/$CCK_PY_ROOT" && $CCK_PY scripts/check_test_drift.py --base main --working --strict )
