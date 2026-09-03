"""MCP stdio server exposing an on-demand AnyBridge browser session."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from .browser import PageBridge
from .builtins import BUILTIN_NAMES, BUILTIN_TOOLS, call_builtin
from .engines import AdaptiveReader
from .profiles import ProfileStore
from .repositories import RepositoryManager, RepositoryStore
from .sites import SiteStore
from .webmcp import publish_tools
from .workflows import WorkflowStore


_NO_BROWSER_NEEDED = {
    "navigate",
    "open_saved_site",
    "list_saved_sites",
    "remove_saved_site",
    "list_saved_repositories",
    "save_repository",
    "open_repository",
    "open_saved_repository",
    "remove_saved_repository",
    "smart_read",
    "engine_status",
    "list_profiles",
    "remove_profile",
    "list_workflows",
    "remove_workflow",
}
_PAGE_MAY_CHANGE = {
    "navigate",
    "open_saved_site",
    "click",
    "submit_form",
    "type_text",
    "call_webmcp_tool",
    "click_ref",
    "fill_ref",
    "select_ref",
    "press_key",
    "run_workflow",
    "open_profile",
    "reset_session",
}
_READ_ONLY = {
    "read_page",
    "list_links",
    "list_forms",
    "current_site",
    "list_webmcp_tools",
    "list_saved_sites",
    "list_saved_repositories",
    "smart_read",
    "engine_status",
    "snapshot",
    "wait_for",
    "extract",
    "screenshot",
    "list_profiles",
    "observe",
    "list_workflows",
}
_DESTRUCTIVE = {
    "submit_form",
    "click",
    "remove_saved_site",
    "remove_saved_repository",
    "click_ref",
    "fill_ref",
    "select_ref",
    "press_key",
    "save_profile",
    "remove_profile",
    "run_workflow",
    "remove_workflow",
}
_LOCAL_ONLY = {
    "list_saved_sites",
    "save_site",
    "remove_saved_site",
    "list_saved_repositories",
    "save_repository",
    "remove_saved_repository",
    "list_profiles",
    "save_profile",
    "remove_profile",
    "list_workflows",
    "save_workflow",
    "remove_workflow",
}


def _to_content(result) -> list[types.TextContent | types.ImageContent]:
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
            elif isinstance(item, dict) and item.get("type") == "image":
                blocks.append(
                    types.ImageContent(
                        type="image",
                        data=str(item.get("data") or ""),
                        mimeType=str(item.get("mimeType") or "image/png"),
                    )
                )
            else:
                blocks.append(
                    types.TextContent(type="text", text=json.dumps(item, ensure_ascii=False))
                )
        if blocks:
            return blocks
    if isinstance(result, str):
        return [types.TextContent(type="text", text=result)]
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


def _tool_annotations(name: str) -> types.ToolAnnotations:
    if name.startswith("webmcp_"):
        return types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        )
    return types.ToolAnnotations(
        readOnlyHint=name in _READ_ONLY,
        destructiveHint=name in _DESTRUCTIVE,
        idempotentHint=name in _READ_ONLY,
        openWorldHint=name not in _LOCAL_ONLY,
    )


class BridgeRuntime:
    """Own the lazy browser and saved-site registry for one MCP connection."""

    def __init__(
        self,
        url: str | None = None,
        *,
        headless: bool = True,
        wait: float = 5.0,
        builtins: bool = True,
        sites: SiteStore | None = None,
        repositories: RepositoryStore | None = None,
        repository_manager: RepositoryManager | None = None,
        profiles: ProfileStore | None = None,
        workflows: WorkflowStore | None = None,
        adaptive: AdaptiveReader | None = None,
        bridge: PageBridge | None = None,
        allow_private_network: bool = True,
    ) -> None:
        self.initial_url = url
        self.wait = wait
        self.builtins = builtins
        self.sites = sites or SiteStore()
        self.repositories = repositories or RepositoryStore()
        self.repository_manager = repository_manager or RepositoryManager()
        self.profiles = profiles or ProfileStore()
        self.workflows = workflows or WorkflowStore()
        self.adaptive = adaptive or AdaptiveReader(
            allow_private_network=allow_private_network
        )
        self.bridge = bridge or PageBridge(
            url,
            headless=headless,
            allow_private_network=allow_private_network,
        )
        self._start_lock = asyncio.Lock()

    async def ensure_browser(self) -> None:
        if self.bridge.started:
            return
        if self.adaptive.browser_retry_seconds:
            raise RuntimeError(
                "The interactive browser route is cooling down after a failure "
                f"({self.adaptive.browser_failure}). Use smart_read or retry in "
                f"{self.adaptive.browser_retry_seconds:.0f}s."
            )
        async with self._start_lock:
            if self.bridge.started:
                return
            try:
                await asyncio.wait_for(self.bridge.start(), timeout=25)
                if self.initial_url:
                    await asyncio.wait_for(
                        self.bridge.discover_tools(
                            timeout=min(max(self.wait, 2.0), 12.0),
                            reload_on_failure=True,
                        ),
                        timeout=18,
                    )
            except Exception as error:
                self.adaptive.note_browser_failure(error)
                try:
                    await asyncio.wait_for(self.bridge.close(), timeout=5)
                except Exception:
                    pass
                raise RuntimeError(
                    "The interactive browser is temporarily unavailable; "
                    "smart_read remains available through cache, HTTP, and Wayback."
                ) from error

    async def tool_specs(self) -> list[dict]:
        # Listing tools must always be immediate. A supplied URL is opened lazily
        # by the first page operation, then WebMCP tools appear via list-changed.
        raw_tools = await self.bridge.list_tools() if self.bridge.started else []
        current = await self.bridge.current_site() if self.bridge.started else {}
        site_tools, _ = publish_tools(raw_tools, current.get("url"))
        if not self.builtins:
            return site_tools
        return list(BUILTIN_TOOLS) + site_tools

    async def _published_site_tools(self) -> tuple[list[dict], dict[str, str]]:
        if not self.bridge.started:
            return [], {}
        current = await self.bridge.current_site()
        return publish_tools(await self.bridge.list_tools(), current.get("url"))

    async def call(self, name: str, arguments: dict) -> object:
        needs_browser = name not in _NO_BROWSER_NEEDED
        if name == "save_site" and arguments.get("url"):
            needs_browser = False
        if needs_browser:
            await self.ensure_browser()

        if self.builtins and name in BUILTIN_NAMES:
            return await call_builtin(
                self.bridge,
                name,
                arguments,
                self.sites,
                self.repositories,
                self.repository_manager,
                self.profiles,
                self.workflows,
                self.adaptive,
            )
        _, site_mapping = await self._published_site_tools()
        if name in site_mapping:
            return await self.bridge.call_tool(site_mapping[name], arguments)
        raise ValueError(f'Unknown AnyBridge tool: "{name}".')

    async def close(self) -> None:
        await self.adaptive.close()
        await self.bridge.close()


def create_server(runtime: BridgeRuntime | None = None) -> Server:
    """Build the low-level MCP server around a runtime."""

    @asynccontextmanager
    async def remote_lifespan(server):
        session_runtime = BridgeRuntime(allow_private_network=False)
        try:
            yield session_runtime
        finally:
            await session_runtime.close()

    @asynccontextmanager
    async def local_lifespan(server):
        yield None

    server = Server(
        "anybridge",
        version="1.0.0",
        instructions=(
            "ROUTING RULE: for every user-supplied website URL, use AnyBridge navigate and inspect "
            "the loaded page; never replace this with web search or WebFetch. If the message is "
            "only a URL, navigate immediately, take a semantic snapshot, discover WebMCP actions, "
            "and give a concise overview without asking what the user wants. For every Git remote, "
            "use open_repository, then continue with native file and code tools at its returned "
            "absolute path. Prefer namespaced WebMCP tools, then exact element refs, and use text/CSS "
            "operations only as compatibility fallbacks. If navigate reports continuity mode, use "
            "its read-only result and do not call snapshot or claim that live actions completed. "
            "For PDF documents use smart_read, or read_page when the browser already shows "
            'the file; both return page-marked text, and pages="12-15" reads one range of a '
            "long document. Never read a PDF by screenshot. A "
            "site without native WebMCP still has the AnyBridge-generated built-in MCP tools. "
            "Consequential website actions require the "
            "user's request. Saved profiles are read-only unless save_profile is explicitly called."
        ),
        lifespan=remote_lifespan if runtime is None else local_lifespan,
    )

    def active_runtime() -> BridgeRuntime:
        if runtime is not None:
            return runtime
        return server.request_context.lifespan_context

    @server.list_tools()
    async def on_list_tools() -> list[types.Tool]:
        current = active_runtime()
        return [
            types.Tool(
                name=tool["name"],
                description=tool["description"],
                inputSchema=tool["inputSchema"],
                annotations=_tool_annotations(tool["name"]),
            )
            for tool in await current.tool_specs()
        ]

    @server.call_tool(validate_input=False)
    async def on_call_tool(name: str, arguments: dict) -> types.CallToolResult:
        current = active_runtime()
        before = {tool["name"] for tool in (await current._published_site_tools())[0]}
        try:
            result = await asyncio.wait_for(
                current.call(name, arguments or {}), timeout=75
            )
            after = {tool["name"] for tool in (await current._published_site_tools())[0]}
            if after != before or name in _PAGE_MAY_CHANGE:
                await server.request_context.session.send_tool_list_changed()
            return types.CallToolResult(content=_to_content(result))
        except asyncio.TimeoutError:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=(
                            "AnyBridge stopped this route at its 75-second deadline. "
                            "The service is still available; use smart_read for a "
                            "read-only continuity result or retry the action."
                        ),
                    )
                ],
                isError=True,
            )
        except Exception as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))],
                isError=True,
            )

    return server



async def serve(
    url: str | None = None,
    headless: bool = True,
    wait: float = 5.0,
    builtins: bool = True,
) -> None:
    """Run the AnyBridge MCP server over stdio."""
    if not url and not builtins:
        raise ValueError("A URL is required when --no-builtins is used.")

    runtime = BridgeRuntime(url, headless=headless, wait=wait, builtins=builtins)
    server = create_server(runtime)
    try:
        async with stdio_server() as (read, write):
            await server.run(
                read,
                write,
                server.create_initialization_options(
                    NotificationOptions(tools_changed=True)
                ),
            )
    finally:
        await runtime.close()
