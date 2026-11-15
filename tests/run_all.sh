#!/bin/sh
# run_all.sh [py|lua|all]: run the plugin's tests. Run from anywhere.
# Python tests use python3's unittest. Lua tests use `lua` (5.1 or LuaJIT)
# when it's on PATH, else `rex do`, else they are skipped with a note.
dir=$(cd "$(dirname "$0")" && pwd)
what=${1:-all}
status=0

if [ "$what" != lua ]; then
  python3 -m unittest discover -s "$dir/py" -t "$dir/py" || status=1
fi
if [ "$what" != py ]; then
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
