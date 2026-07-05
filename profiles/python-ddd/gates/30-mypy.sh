#!/usr/bin/env bash
set -uo pipefail; . "$(dirname "$0")/_profile.sh"
[ -n "$(src_staged)" ] || exit 0
( cd "$PYDIR" && $CCK_MYPY )
