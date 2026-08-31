"""MCP stdio server that mirrors the WebMCP tools of a live page.

Any MCP client (Claude, OpenAI Agents SDK, ...) connects over stdio; every
tool call is executed inside the real page via the shim.
"""

import json

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .browser import PageBridge


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


async def serve(url: str, headless: bool = True, wait: float = 10.0):
    bridge = PageBridge(url, headless=headless)
    await bridge.start()
    await bridge.wait_for_tools(timeout=wait)

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=t["name"],
                    description=t["description"],
                    input_schema=t["inputSchema"],
                )
                for t in await bridge.list_tools()
            ]
        )

    async def on_call_tool(ctx, params) -> types.CallToolResult:
        try:
            result = await bridge.call_tool(params.name, params.arguments or {})
        except Exception as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], is_error=True
            )
        return types.CallToolResult(content=_to_content(result))

    server = Server(
        "anybridge",
        instructions=f"Tools registered by the page at {url}, proxied live via WebMCP.",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )

    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await bridge.close()
