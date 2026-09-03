"""Non-destructive live acceptance test for AnyBridge's reference sites."""

from __future__ import annotations

import asyncio
import json
import time

from anybridge.browser import PageBridge
from anybridge.engines import AdaptiveReader
from anybridge.webmcp import publish_tools


async def check_fast_reads() -> list[dict]:
    reader = AdaptiveReader(cache_seconds=0)
    results = []
    for url, expected in (
        ("https://www.lasosta-beb.it/", "sosta"),
        ("https://www.bauzaar.it/", "bauzaar"),
    ):
        started = time.monotonic()
        result = await reader.read(url, prefer="auto")
        if expected not in result.content.casefold():
            raise AssertionError(f"{url} did not return expected public content")
        results.append(
            {
                "site": url,
                "engine": result.engine,
                "seconds": round(time.monotonic() - started, 2),
            }
        )
    return results


async def check_lasosta() -> dict:
    bridge = PageBridge("https://www.lasosta-beb.it/")
    started = time.monotonic()
    try:
        await bridge.start(settle=0.5)
        snapshot = await bridge.snapshot(max_chars=5000)
        if "LE CAMERE" not in snapshot.upper():
            raise AssertionError("La Sosta semantic snapshot has no room link")
        tools = await bridge.discover_tools(timeout=5)
        return {
            "site": bridge.current_url,
            "semantic_refs": snapshot.count("@e"),
            "webmcp_tools": len(tools),
            "seconds": round(time.monotonic() - started, 2),
        }
    finally:
        await bridge.close()


async def check_bauzaar() -> dict:
    bridge = PageBridge("https://www.bauzaar.it/")
    started = time.monotonic()
    try:
        await bridge.start(settle=0.5)
        tools = await bridge.discover_tools(timeout=25, reload_on_failure=True)
        names = {tool["name"] for tool in tools}
        required = {"search_catalog", "get_product", "get_cart", "update_cart"}
        if not required.issubset(names):
            raise AssertionError(f"Bauzaar WebMCP tools missing: {sorted(required - names)}")
        published, _ = publish_tools(tools, bridge.current_url)
        result = await bridge.call_tool(
            "search_catalog",
            {"catalog": {"query": "crocchette cane", "pagination": {"limit": 2}}},
        )
        rendered = json.dumps(result, ensure_ascii=False)
        if "product" not in rendered.casefold():
            raise AssertionError("Bauzaar catalog search returned no product data")
        return {
            "site": bridge.current_url,
            "shopify_webmcp_tools": len(tools),
            "published_prefix_ok": all(
                tool["name"].startswith("webmcp_www_bauzaar_it_")
                for tool in published
            ),
            "catalog_search": "ok",
            "seconds": round(time.monotonic() - started, 2),
        }
    finally:
        await bridge.close()


async def main() -> None:
    report = {
        "adaptive": await check_fast_reads(),
        "lasosta": await check_lasosta(),
        "bauzaar": await check_bauzaar(),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
