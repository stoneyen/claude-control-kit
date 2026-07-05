# sourced by python-ddd gates: loads profile.env, defines helpers.
ROOT="$(git rev-parse --show-toplevel)"
[ -f "$ROOT/.dev/gates/profile.env" ] && . "$ROOT/.dev/gates/profile.env"
: "${CCK_PKG:=mywb}"; : "${CCK_PY_ROOT:=backend}"; : "${CCK_PY:=uv run python}"
: "${CCK_RUFF:=uv run ruff}"; : "${CCK_MYPY:=uv run mypy}"
export CCK_PKG
PYDIR="$ROOT/$CCK_PY_ROOT"
# staged files (relative to repo root) come on stdin
STAGED="$(cat)"
# staged .py under the python root (relative to $CCK_PY_ROOT), or empty
py_staged() { printf '%s\n' "$STAGED" | grep -E "^${CCK_PY_ROOT%/}/.+\.py$" || true; }
src_staged() { printf '%s\n' "$STAGED" | grep -E "^${CCK_PY_ROOT%/}/src/${CCK_PKG}/" || true; }
