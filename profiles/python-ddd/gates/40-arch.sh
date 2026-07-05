#!/usr/bin/env bash
# Clean Architecture / DDD layering lint (validate_arch.py). Blocks on any
# forbidden cross-layer import, float-money, aggregate-filter divergence, etc.
set -uo pipefail; . "$(dirname "$0")/_profile.sh"
[ -n "$(src_staged)" ] || exit 0
( cd "$PYDIR" && CCK_PKG="$CCK_PKG" $CCK_PY scripts/validate_arch.py )
