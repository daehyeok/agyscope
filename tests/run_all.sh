#!/bin/sh
# run_all.sh [py|lua|all]: run the plugin's tests. Run from anywhere.
# Python tests run with pytest in the project's uv environment (Python ${UV_PYTHON:-3.13});
# uv is required. Lua tests use `lua` (5.1 or LuaJIT) when it's on PATH, else `rex do`,
# else they are skipped with a note.
dir=$(cd "$(dirname "$0")" && pwd)
plugin_dir=$(cd "$dir/.." && pwd)
what=${1:-all}
status=0

case "$what" in
  py|lua|all) ;;
  *)
    echo "run_all: unknown target '$what'; expected py, lua, or all" >&2
    exit 1
    ;;
esac

if [ "$what" = py ] || [ "$what" = all ]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "run_all: uv not found (https://docs.astral.sh/uv/)" >&2
    exit 1
  fi
  uv run --project "$plugin_dir" --python "${UV_PYTHON:-3.13}" pytest -q "$dir/py" || status=1
fi
if [ "$what" = lua ] || [ "$what" = all ]; then
  if command -v lua >/dev/null 2>&1; then run="lua"
  elif command -v rex >/dev/null 2>&1; then run="rex do"
  else
    echo "run_all: neither lua nor rex found; Lua tests skipped" >&2
    exit $status
  fi
  for t in "$dir"/lua/*_test.lua; do
    # $run is one or two words on purpose
    # shellcheck disable=SC2086
    out=$($run "$t" 2>&1) || status=1
    printf '%s\n' "$out" | grep -v '^"ok"$'
    case $out in *" passed, 0 failed"*) ;; *) status=1 ;; esac
  done
fi
exit $status
