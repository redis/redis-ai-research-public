#!/usr/bin/env bash
# Register every MCP source config in this folder with the running service,
# one POST per file — plus any extra sources (paths or URLs) passed as args.
#
# Usage:
#   ./register.sh                                   # register all *.json here
#   ./register.sh /path/to/my_server.py             # ...plus extra sources
#   ./register.sh --replace                         # clear all sources first
#
# Env overrides: API_URL (default http://localhost:8000), API_KEY (demo key).
set -euo pipefail

API_URL=${API_URL:-http://localhost:8000}
API_KEY=${API_KEY:-demo-key-acme}
HERE=$(cd "$(dirname "$0")" && pwd)

auth=(-H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json")

if [ "${1:-}" = "--replace" ]; then
  shift
  echo "Clearing existing sources..."
  curl -sf -X DELETE "$API_URL/v1/mcp" "${auth[@]}" > /dev/null
fi

register() {
  local src=$1
  echo -n "Registering $src ... "
  if out=$(curl -sf -m 240 -X POST "$API_URL/v1/mcp/sources" "${auth[@]}" \
      -d "{\"path\": \"$src\"}"); then
    echo "OK"
  else
    echo "FAILED (is the server running? is the path allowed?)"
    return 1
  fi
}

failures=0
for f in "$HERE"/*.json; do
  [ -e "$f" ] || continue
  register "$f" || failures=$((failures + 1))
done
for extra in "$@"; do
  register "$extra" || failures=$((failures + 1))
done

echo
echo "Current inventory:"
curl -sf "$API_URL/v1/mcp" "${auth[@]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for s in d['servers']:
    print('  ' + s['name'] + ': ' + str(len(s['tools'])) + ' tools')
"
exit $failures
