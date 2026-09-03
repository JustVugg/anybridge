"""anybridge CLI: expose any web page to any agent."""

import argparse
import asyncio
import json
import os
import sys

from .browser import PageBridge
from .builtins import BUILTIN_NAMES, BUILTIN_TOOLS
from .repositories import RepositoryError, RepositoryManager, RepositoryStore
from .sites import SiteStore, SiteStoreError
from .webmcp import publish_tools


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
        raw = await bridge.discover_tools(
            timeout=args.wait, reload_on_failure=True
        )
        current = await bridge.current_site()
        site, _ = publish_tools(raw, current.get("url"))
        builtins = [] if args.no_builtins else BUILTIN_TOOLS
        _print_tools(site, builtins, args.json)


async def _call(args):
    tool_args = json.loads(args.args)
    if args.tool in BUILTIN_NAMES and not args.no_builtins:
        from .server import BridgeRuntime

        initial_url = None if args.tool in {"navigate", "smart_read"} else args.url
        runtime = BridgeRuntime(
            initial_url,
            headless=not args.headed,
            wait=args.wait,
        )
        if args.tool in {"navigate", "smart_read"}:
            tool_args.setdefault("url", args.url)
        try:
            print(await runtime.call(args.tool, tool_args))
        finally:
            await runtime.close()
        return
    async with PageBridge(args.url, headless=not args.headed) as bridge:
        raw = await bridge.discover_tools(
            timeout=args.wait, reload_on_failure=True
        )
        current = await bridge.current_site()
        _, mapping = publish_tools(raw, current.get("url"))
        if args.tool not in mapping:
            raise ValueError(f'Unknown namespaced WebMCP tool: "{args.tool}".')
        result = await bridge.call_tool(mapping[args.tool], tool_args)
        print(json.dumps(result, indent=2, ensure_ascii=False))


async def _serve(args):
    from .server import serve

    await serve(
        args.url,
        headless=not args.headed,
        wait=args.wait,
        builtins=not args.no_builtins,
    )


async def _sites_list(args):
    sites = SiteStore().list()
    if args.json:
        print(
            json.dumps(
                [{"name": site.name, "url": site.url} for site in sites],
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    if not sites:
        print("No saved sites.")
        return
    for site in sites:
        print(f"{site.name}\t{site.url}")


async def _sites_add(args):
    site = SiteStore().save(args.name, args.url)
    print(f'Saved "{site.name}" as {site.url}.')


async def _sites_remove(args):
    site = SiteStore().remove(args.name)
    print(f'Removed "{site.name}".')


async def _repos_list(args):
    repositories = RepositoryStore().list()
    if args.json:
        print(
            json.dumps(
                [
                    {"name": repository.name, "url": repository.url}
                    for repository in repositories
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    if not repositories:
        print("No saved repositories.")
        return
    for repository in repositories:
        print(f"{repository.name}\t{repository.url}")


async def _repos_add(args):
    repository = RepositoryStore().save(args.name, args.url)
    print(f'Saved repository "{repository.name}" as {repository.url}.')


async def _repos_remove(args):
    repository = RepositoryStore().remove(args.name)
    print(f'Removed repository alias "{repository.name}"; local files were not deleted.')


async def _repos_open(args):
    prepared = await RepositoryManager().prepare_async(args.url)
    state = "Cloned" if prepared.cloned else "Reopened"
    print(f"{state} {prepared.url}\n{prepared.path}")


def main():
    parser = argparse.ArgumentParser(
        prog="anybridge",
        description=(
            "Open websites and Git repositories inside MCP-capable coding agents."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    def common(p, *, optional_url=False):
        p.add_argument(
            "url",
            nargs="?" if optional_url else None,
            help="Page URL (http(s):// or file://)",
        )
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
    common(p_serve, optional_url=True)
    p_serve.set_defaults(func=_serve)

    p_sites = sub.add_parser("sites", help="Manage saved site aliases")
    sites_sub = p_sites.add_subparsers(dest="sites_command", required=True)

    p_sites_list = sites_sub.add_parser("list", help="List saved sites")
    p_sites_list.add_argument("--json", action="store_true", help="Output raw JSON")
    p_sites_list.set_defaults(func=_sites_list)

    p_sites_add = sites_sub.add_parser("add", help="Save a site")
    p_sites_add.add_argument("name", help="Site name or alias")
    p_sites_add.add_argument("url", help="Website URL")
    p_sites_add.set_defaults(func=_sites_add)

    p_sites_remove = sites_sub.add_parser("remove", help="Remove a saved site")
    p_sites_remove.add_argument("name", help="Site name or alias")
    p_sites_remove.set_defaults(func=_sites_remove)

    p_repos = sub.add_parser("repos", help="Manage saved Git repositories")
    repos_sub = p_repos.add_subparsers(dest="repos_command", required=True)

    p_repos_list = repos_sub.add_parser("list", help="List saved repositories")
    p_repos_list.add_argument("--json", action="store_true", help="Output raw JSON")
    p_repos_list.set_defaults(func=_repos_list)

    p_repos_add = repos_sub.add_parser("add", help="Save a repository alias")
    p_repos_add.add_argument("name", help="Repository name or alias")
    p_repos_add.add_argument("url", help="Git repository URL")
    p_repos_add.set_defaults(func=_repos_add)

    p_repos_remove = repos_sub.add_parser("remove", help="Remove a repository alias")
    p_repos_remove.add_argument("name", help="Repository name or alias")
    p_repos_remove.set_defaults(func=_repos_remove)

    p_repos_open = repos_sub.add_parser("open", help="Clone or reopen a repository")
    p_repos_open.add_argument("url", help="Git repository URL")
    p_repos_open.set_defaults(func=_repos_open)

    p_remote = sub.add_parser(
        "remote", help="Run a cloud-ready Streamable HTTP MCP server"
    )
    p_remote.add_argument("--host", default="127.0.0.1")
    p_remote.add_argument("--port", type=int, default=8765)
    p_remote.add_argument(
        "--token",
        default=None,
        help="Bearer token (or set ANYBRIDGE_API_TOKEN); required outside localhost",
    )
    p_remote.add_argument("--allowed-host", action="append", default=[])
    p_remote.add_argument("--allowed-origin", action="append", default=[])
    p_remote.add_argument("--idle-timeout", type=float, default=900)

    args = parser.parse_args()
    if args.command is None:
        from .tui import main as tui_main

        tui_main()
        return
    if args.command == "serve" and args.url is None and args.no_builtins:
        parser.error("anybridge serve --no-builtins requires a URL")
    try:
        if args.command == "remote":
            from .remote import run_remote

            run_remote(
                host=args.host,
                port=args.port,
                api_token=args.token or os.environ.get("ANYBRIDGE_API_TOKEN"),
                allowed_hosts=args.allowed_host,
                allowed_origins=args.allowed_origin,
                idle_timeout=args.idle_timeout,
            )
            return
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except (RepositoryError, SiteStoreError, ValueError) as error:
        parser.exit(2, f"anybridge: error: {error}\n")


if __name__ == "__main__":
    main()
