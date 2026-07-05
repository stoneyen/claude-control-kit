#!/usr/bin/env bash
# entity-spec.md <-> use-case yaml sync (check_spec_sync.py).
set -uo pipefail; . "$(dirname "$0")/_profile.sh"
[ -n "$(src_staged)" ] || exit 0
( cd "$PYDIR" && $CCK_PY scripts/check_spec_sync.py )
