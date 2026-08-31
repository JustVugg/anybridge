"""anybridge CLI: expose any web page to any agent."""

import argparse
import asyncio
import json
import sys

from .browser import PageBridge
from .builtins import BUILTIN_NAMES, BUILTIN_TOOLS, call_builtin


def _print_tools(site: list[dict], builtins: list[dict], as_json: bool):
    if as_json:
        print(json.dumps({"webmcp": site, "builtin": builtins}, indent=2, ensure_ascii=False))
        return
    if site:
        print("WebMCP tools registered by the site:")
        _print_group(site)
    else:
        print("No WebMCP tools registered by this site.")
    if builtins:
        print("\nBuilt-in tools (work on any site):")
        _print_group(builtins)


def _print_group(tools: list[dict]):
    for t in tools:
        params = ", ".join((t["inputSchema"].get("properties") or {}).keys())
        print(f"  {t['name']}({params})")
        if t["description"]:
            print(f"      {t['description']}")


async def _list(args):
    async with PageBridge(args.url, headless=not args.headed) as bridge:
        site = await bridge.wait_for_tools(timeout=args.wait)
        builtins = [] if args.no_builtins else BUILTIN_TOOLS
        _print_tools(site, builtins, args.json)


async def _call(args):
    async with PageBridge(args.url, headless=not args.headed) as bridge:
        tool_args = json.loads(args.args)
        if args.tool in BUILTIN_NAMES and not args.no_builtins:
            site = await bridge.list_tools()
            if args.tool not in {t["name"] for t in site}:
                print(await call_builtin(bridge, args.tool, tool_args))
                return
        else:
            await bridge.wait_for_tools(timeout=args.wait)
        result = await bridge.call_tool(args.tool, tool_args)
        print(json.dumps(result, indent=2, ensure_ascii=False))


async def _serve(args):
    from .server import serve

    await serve(
        args.url,
        headless=not args.headed,
        wait=args.wait,
        builtins=not args.no_builtins,
    )


def main():
    parser = argparse.ArgumentParser(
        prog="anybridge",
        description="Expose any web page to any agent, as a standard MCP server.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("url", help="Page URL (http(s):// or file://)")
        p.add_argument("--headed", action="store_true", help="Show the browser window")
        p.add_argument(
            "--wait", type=float, default=5.0, help="Max seconds to wait for WebMCP tools (default 5)"
        )
        p.add_argument(
            "--no-builtins",
            action="store_true",
            help="Expose only the site's own WebMCP tools",
        )

    p_list = sub.add_parser("list", help="List the tools available on a page")
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
