"""anybridge CLI: inspect and serve the WebMCP tools of any web page."""

import argparse
import asyncio
import json
import sys

from .browser import PageBridge


def _print_tools(tools: list[dict], as_json: bool):
    if as_json:
        print(json.dumps(tools, indent=2, ensure_ascii=False))
        return
    if not tools:
        print("No WebMCP tools found on this page.")
        return
    for t in tools:
        params = ", ".join((t["inputSchema"].get("properties") or {}).keys())
        print(f"  {t['name']}({params})")
        if t["description"]:
            print(f"      {t['description']}")


async def _list(args):
    async with PageBridge(args.url, headless=not args.headed) as bridge:
        tools = await bridge.wait_for_tools(timeout=args.wait)
        _print_tools(tools, args.json)


async def _call(args):
    async with PageBridge(args.url, headless=not args.headed) as bridge:
        await bridge.wait_for_tools(timeout=args.wait)
        result = await bridge.call_tool(args.tool, json.loads(args.args))
        print(json.dumps(result, indent=2, ensure_ascii=False))


async def _serve(args):
    from .server import serve

    await serve(args.url, headless=not args.headed, wait=args.wait)


def main():
    parser = argparse.ArgumentParser(
        prog="anybridge",
        description="Expose any web page's WebMCP tools to any agent, as a standard MCP server.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("url", help="Page URL (http(s):// or file://)")
        p.add_argument("--headed", action="store_true", help="Show the browser window")
        p.add_argument(
            "--wait", type=float, default=10.0, help="Max seconds to wait for tools (default 10)"
        )

    p_list = sub.add_parser("list", help="List the tools a page registers")
    common(p_list)
    p_list.add_argument("--json", action="store_true", help="Output raw JSON")
    p_list.set_defaults(func=_list)

    p_call = sub.add_parser("call", help="Call one tool and print the result")
    common(p_call)
    p_call.add_argument("tool", help="Tool name")
    p_call.add_argument("--args", default="{}", help='Tool arguments as JSON (default "{}")')
    p_call.set_defaults(func=_call)

    p_serve = sub.add_parser("serve", help="Run an MCP stdio server for the page")
    common(p_serve)
    p_serve.set_defaults(func=_serve)

    args = parser.parse_args()
    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
