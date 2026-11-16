#!/bin/sh
# run_all.sh [py|lua|uv|all]: run the plugin's tests. Run from anywhere.
# Python tests use python3's unittest. The uv suite runs all Python tests
# including Textual in the plugin venv. Lua tests use `lua` (5.1 or LuaJIT)
# when it's on PATH, else `rex do`, else they are skipped with a note.
dir=$(cd "$(dirname "$0")" && pwd)
plugin_dir=$(cd "$dir/.." && pwd)
what=${1:-all}
status=0

case "$what" in
  py|lua|uv|all) ;;
  *)
    echo "run_all: unknown target '$what'; expected py, lua, uv, or all" >&2
    exit 1
    ;;
esac

if [ "$what" = py ] || [ "$what" = all ]; then
  python3 -m unittest discover -s "$dir/py" -t "$dir/py" || status=1
fi
if [ "$what" = uv ] || [ "$what" = all ]; then
  if command -v uv >/dev/null 2>&1; then
    uv run --project "$plugin_dir" python -m unittest discover -s "$dir/py" -t "$dir/py" || status=1
  elif [ "${REQUIRE_UV:-}" = 1 ]; then
    echo "run_all: uv not found" >&2
    exit 1
  else
    echo "run_all: uv not found; Textual tests skipped" >&2
  fi
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
