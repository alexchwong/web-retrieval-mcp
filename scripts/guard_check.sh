#!/usr/bin/env bash
# Request-guard check on the running Compose stack (Section 15, test 10a, Compose half).
#
# MCP requests must succeed from the host at 127.0.0.1:${MCP_HOST_PORT:-8765},
# from inside the container, and from a second container on the Compose network
# using the service name. Host: evil.example must get 421 and
# Origin: http://evil.example 403, and the server's tool-call log must show that
# no tool ran for either.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

port="${MCP_HOST_PORT:-8765}"
host_url="http://127.0.0.1:${port}/mcp"
call='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"web_page_text","arguments":{"url":"file:///guard-check"}}}'
failed=0

pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failed=1; }

tool_calls() {
    docker compose logs --no-color web-retrieval 2>/dev/null | grep -c 'webSpell tool call:' || true
}

status_from_host() {  # status_from_host [extra curl args...]
    curl -sS -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 10 -X POST "$host_url" \
        -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
        "$@" --data "$call"
}

probe='
import json, sys, urllib.request
request = urllib.request.Request(sys.argv[1], data=sys.argv[2].encode(), method="POST", headers={
    "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
print(opener.open(request, timeout=10).status)
'

before="$(tool_calls)"
[[ "$(status_from_host)" == 200 ]] && pass "host 127.0.0.1:${port}" || fail "host 127.0.0.1:${port}"
inside="$(docker compose exec -T web-retrieval python -c "$probe" "http://127.0.0.1:8765/mcp" "$call" || true)"
[[ "$inside" == 200 ]] && pass "inside the container" || fail "inside the container"
second="$(docker compose run --rm --no-deps -T --entrypoint python web-retrieval -c "$probe" \
          "http://web-retrieval:8765/mcp" "$call" 2>/dev/null | tail -n 1 || true)"
[[ "$second" == 200 ]] && pass "second container via service name" || fail "second container via service name"
sleep 1
after_good="$(tool_calls)"
(( after_good - before == 3 )) && pass "three tool calls logged" || fail "expected three tool calls, saw $((after_good - before))"

[[ "$(status_from_host -H 'Host: evil.example')" == 421 ]] && pass "Host: evil.example refused (421)" \
    || fail "Host: evil.example not refused with 421"
[[ "$(status_from_host -H 'Origin: http://evil.example')" == 403 ]] && pass "Origin: http://evil.example refused (403)" \
    || fail "Origin: http://evil.example not refused with 403"
sleep 1
(( "$(tool_calls)" == after_good )) && pass "no tool ran for refused requests" || fail "a refused request reached a tool"
exit "$failed"
