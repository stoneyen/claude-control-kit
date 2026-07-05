#!/usr/bin/env bash
set -uo pipefail; . "$(dirname "$0")/_profile.sh"
[ -n "$(py_staged)" ] || exit 0
( cd "$PYDIR" && $CCK_RUFF check . )
