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
    "${SMOKE_URL:-https://example.com/}" "${SMOKE_CHECK_W3C_PDF:-0}" \
    "${SMOKE_LOG_RESPONSES:-0}" <<'PY'
import asyncio
import json
import os
import sys

from fastmcp import Client

# The probe connects only to loopback inside the container.
for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(name, None)
    os.environ.pop(name.lower(), None)

selected, query, url, check_w3c_pdf, log_responses = sys.argv[1:]
w3c_pdf_url = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
expected = ("web_search_results", "web_evidence_passages", "web_page_text")


class ProbeFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ProbeFailure(message)


async def call(client, tool, args, *, label=None):
    result = await client.call_tool(tool, args, raise_on_error=False)
    payload = result.structured_content
    if log_responses == "1":
        body = payload
        if result.is_error or body is None:
            body = [block.model_dump(mode="json") for block in result.content]
        print(f"MCP response for {label or tool} (is_error={result.is_error})", flush=True)
        # Keep external text escaped and prefix every line in the Actions log.
        for line in json.dumps(body, indent=2, ensure_ascii=True).splitlines():
            print(f"  | {line}", flush=True)
    require(not result.is_error, "MCP tool error")
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
        data = await call(client, "web_evidence_passages", args, label=tool)
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
        pdf = tool == "web_page_text_pdf"
        data = await call(
            client, "web_page_text", {"url": w3c_pdf_url if pdf else url}, label=tool
        )
        require(data.get("status") == "complete" and data.get("fetch_status") == "fetched",
                f"status={data.get('status')} fetch_status={data.get('fetch_status')}")
        require(data.get("text") and data.get("chars") == len(data["text"])
                and data.get("origin") in {"fetched_page", "fetched_pdf"},
                "missing text or inconsistent extraction metadata")
        if pdf:
            require(data.get("origin") == "fetched_pdf", "expected PDF extraction")
            require("Dummy PDF file" in data["text"], "expected W3C PDF text missing")


async def run():
    async with Client("http://127.0.0.1:8765/mcp") as client:
        tools = await asyncio.wait_for(client.list_tools(), timeout=15)
        require({tool.name for tool in tools} == set(expected), "unexpected tool list")
        print("PASS MCP tool registration", flush=True)
        failed = False
        checks = ("web_search_results", "web_evidence_passages",
                  "web_evidence_passages_urls", "web_page_text")
        if check_w3c_pdf == "1":
            checks += ("web_page_text_pdf",)
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
        return await asyncio.wait_for(run(), timeout=335 if check_w3c_pdf == "1" else 275)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ProbeFailure) else type(exc).__name__
        print(f"FAIL MCP connection or registration — {detail}", file=sys.stderr)
        return 1


raise SystemExit(asyncio.run(main()))
PY
