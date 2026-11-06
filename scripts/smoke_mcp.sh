#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

tool="${1:-all}"
if [[ $# -gt 1 ]]; then
    tool=invalid
fi
case "$tool" in
    all|web_search_results|web_evidence_passages|web_evidence_passages_urls|web_page_text) ;;
    *) echo "Usage: bash scripts/smoke_mcp.sh [all|web_search_results|web_evidence_passages|web_evidence_passages_urls|web_page_text]" >&2; exit 2 ;;
esac

docker compose exec -T web-retrieval python - "$tool" \
    "${SMOKE_QUERY:-IANA example domains}" \
    "${SMOKE_URL:-https://example.com/}" <<'PY'
import asyncio
import os
import sys

from fastmcp import Client

# The probe connects only to loopback inside the container.
for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(name, None)
    os.environ.pop(name.lower(), None)

selected, query, url = sys.argv[1:]
expected = ("web_search_results", "web_evidence_passages", "web_page_text")


class ProbeFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ProbeFailure(message)


async def call(client, tool, args):
    result = await client.call_tool(tool, args, raise_on_error=False)
    require(not result.is_error, "MCP tool error")
    payload = result.structured_content
    require(isinstance(payload, dict), "missing structured result")
    return payload


async def check(client, tool):
    if tool == "web_search_results":
        data = await call(client, tool, {"query": query})
        require(data.get("status") == "complete", f"status={data.get('status')}")
        require(data.get("sources"), "no search sources")
        require("passages" not in data, "unexpected passages")
        require(
            all(source.get("fetch_status") == "not_attempted"
                and source.get("origin") == "search_snippet"
                and source.get("source_id") and source.get("url")
                for source in data["sources"]),
            "unexpected browse provenance",
        )
    elif tool in {"web_evidence_passages", "web_evidence_passages_urls"}:
        supplied = tool == "web_evidence_passages_urls"
        args = {"query": query, "max_passages": 2}
        if supplied:
            args["urls"] = [url]
        data = await call(client, "web_evidence_passages", args)
        require(data.get("mode") == ("supplied_urls" if supplied else "web_search"),
                "wrong evidence mode")
        if supplied:
            require("search" not in data and len(data.get("sources", [])) == 1
                    and data["sources"][0].get("url") == url
                    and data["sources"][0].get("origin") == "supplied_url"
                    and "snippet" not in data["sources"][0],
                    "unexpected supplied-URL provenance")
        require(data.get("status") in {"complete", "partial"},
                f"status={data.get('status')} reason={data.get('reason')}")
        passages = data.get("passages", [])
        require(1 <= len(passages) <= 2, "expected one or two fetched passages")
        sources = {source["source_id"]: source for source in data["sources"]}
        require(
            all(passage.get("text") and "score" in passage
                and passage.get("origin") in {"fetched_page", "fetched_pdf"}
                and sources.get(passage.get("source_id"), {}).get("fetch_status") == "fetched"
                for passage in passages),
            "passages do not link to fetched sources",
        )
    else:
        data = await call(client, tool, {"url": url})
        require(data.get("status") == "complete" and data.get("fetch_status") == "fetched",
                f"status={data.get('status')} fetch_status={data.get('fetch_status')}")
        require(data.get("text") and data.get("chars") == len(data["text"])
                and data.get("origin") in {"fetched_page", "fetched_pdf"},
                "missing text or inconsistent extraction metadata")


async def run():
    async with Client("http://127.0.0.1:8765/mcp") as client:
        tools = await asyncio.wait_for(client.list_tools(), timeout=15)
        require({tool.name for tool in tools} == set(expected), "unexpected tool list")
        print("PASS MCP tool registration", flush=True)
        failed = False
        checks = ("web_search_results", "web_evidence_passages",
                  "web_evidence_passages_urls", "web_page_text")
        for tool in checks if selected == "all" else (selected,):
            try:
                await asyncio.wait_for(check(client, tool), timeout=60)
            except Exception as exc:
                detail = str(exc) if isinstance(exc, ProbeFailure) else type(exc).__name__
                print(f"FAIL {tool} — {detail}", file=sys.stderr, flush=True)
                failed = True
            else:
                print(f"PASS {tool}", flush=True)
        return int(failed)


async def main():
    try:
        return await asyncio.wait_for(run(), timeout=275)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ProbeFailure) else type(exc).__name__
        print(f"FAIL MCP connection or registration — {detail}", file=sys.stderr)
        return 1


raise SystemExit(asyncio.run(main()))
PY
