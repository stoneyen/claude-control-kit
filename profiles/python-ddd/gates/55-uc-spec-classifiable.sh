#!/usr/bin/env bash
# newly-added usecase/*.yaml must carry a detect_type discriminator.
set -uo pipefail; . "$(dirname "$0")/_profile.sh"
specs="$(printf '%s\n' "$STAGED" | grep -E '\.dev/specs/.+/usecase/.+\.yaml$' || true)"
[ -n "$specs" ] || exit 0
( cd "$ROOT" && $CCK_PY "$CCK_PY_ROOT/scripts/check_uc_spec_classifiable.py" $specs )
