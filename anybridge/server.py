"""MCP stdio server that exposes any web page to any agent.

Tools come from two sources, merged:
- WebMCP tools the site itself registers (best quality, when present)
- built-in universal tools (read/navigate/links/forms/click) that work on any site

Every call is executed inside the real page.
"""

import json

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .browser import PageBridge
from .builtins import BUILTIN_TOOLS, call_builtin


def _to_content(result) -> list[types.TextContent]:
    """Normalize a WebMCP execute() result into MCP content blocks."""
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        blocks = []
        for item in result["content"]:
            if isinstance(item, dict) and item.get("type") == "text":
                blocks.append(
                    types.TextContent(
                        type="text", text=str(item.get("text") or item.get("value") or "")
                    )
                )
            else:
                blocks.append(
                    types.TextContent(type="text", text=json.dumps(item, ensure_ascii=False))
                )
        if blocks:
            return blocks
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


async def merged_tools(bridge: PageBridge, builtins: bool = True) -> list[dict]:
    """WebMCP tools from the live page, plus builtins (site names win collisions)."""
    site_tools = await bridge.list_tools()
    site_names = {t["name"] for t in site_tools}
    merged = list(site_tools)
    if builtins:
        merged += [t for t in BUILTIN_TOOLS if t["name"] not in site_names]
    return merged


async def serve(url: str, headless: bool = True, wait: float = 5.0, builtins: bool = True):
    bridge = PageBridge(url, headless=headless)
    await bridge.start()
    if not builtins:
        await bridge.wait_for_tools(timeout=wait)

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=t["name"],
                    description=t["description"],
                    input_schema=t["inputSchema"],
                )
                for t in await merged_tools(bridge, builtins)
            ]
        )

    async def on_call_tool(ctx, params) -> types.CallToolResult:
        args = params.arguments or {}
        try:
            site_names = {t["name"] for t in await bridge.list_tools()}
            if params.name in site_names:
                return types.CallToolResult(
                    content=_to_content(await bridge.call_tool(params.name, args))
                )
            text = await call_builtin(bridge, params.name, args)
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)])
        except Exception as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], is_error=True
            )

    server = Server(
        "anybridge",
        instructions=(
            f"Live browser session on {url}. Site-registered WebMCP tools (if any) plus "
            "universal tools: read_page, navigate, list_links, list_forms, submit_form, click."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )

    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await bridge.close()
