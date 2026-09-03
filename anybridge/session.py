"""Use anybridge as a library, inside any agent framework.

    async with BridgeSession("https://example.com") as site:
        tools = site.langchain_tools()      # LangChain / LangGraph
        schemas = site.openai_tools()       # OpenAI-style function schemas
        specs = site.tool_specs()           # plain dicts, any framework
        text = await site.call("read_page", {})

One browser session backs every tool, so an agent's calls share page state:
click a link, then read the page it landed on.
"""

import json

from .browser import PageBridge
from .builtins import BUILTIN_TOOLS, call_builtin
from .engines import AdaptiveReader
from .profiles import ProfileStore
from .repositories import RepositoryManager, RepositoryStore
from .sites import SiteStore
from .webmcp import publish_tools
from .workflows import WorkflowStore


def result_to_text(result) -> str:
    """Flatten any tool result into plain text, the one shape every framework takes."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        parts = []
        for item in result["content"]:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or item.get("value") or ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        if parts:
            return "\n".join(parts)
    return json.dumps(result, ensure_ascii=False)


class BridgeSession:
    """A live page, exposed as tools for any agent framework."""

    def __init__(self, url: str, headless: bool = True, builtins: bool = True):
        self.url = url
        self.builtins = builtins
        self._bridge = PageBridge(headless=headless)
        self._continuity_content = ""
        self._site_tools: list[dict] = []
        self._site_mapping: dict[str, str] = {}
        self._sites = SiteStore()
        self._repositories = RepositoryStore()
        self._repository_manager = RepositoryManager()
        self._profiles = ProfileStore()
        self._workflows = WorkflowStore()
        self._adaptive = AdaptiveReader()

    async def open(self, wait: float = 30.0) -> "BridgeSession":
        self._continuity_content = await self._adaptive.navigate(
            self.url, bridge=self._bridge
        )
        raw = await self._bridge.list_tools() if self._bridge.started else []
        current = (
            await self._bridge.current_site()
            if self._bridge.started
            else {"url": self.url}
        )
        self._site_tools, self._site_mapping = publish_tools(raw, current.get("url"))
        return self

    async def close(self):
        await self._adaptive.close()
        await self._bridge.close()

    async def __aenter__(self):
        return await self.open()

    async def __aexit__(self, *exc):
        await self.close()

    @property
    def page(self) -> PageBridge:
        """The underlying browser session, for anything the tools don't cover."""
        return self._bridge

    def tool_specs(self) -> list[dict]:
        """Tools as plain dicts: name, description, input_schema (JSON Schema)."""
        tools = []
        if self.builtins:
            tools += list(BUILTIN_TOOLS)
        tools += list(self._site_tools)
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["inputSchema"],
            }
            for t in tools
        ]

    async def refresh(self):
        """Re-read the page's WebMCP tools (they can change as the page navigates)."""
        current = await self._bridge.current_site()
        self._site_tools, self._site_mapping = publish_tools(
            await self._bridge.discover_tools(), current.get("url")
        )

    async def call(self, name: str, args: dict | None = None) -> str:
        """Run one tool in the live page and return its result as text."""
        args = args or {}
        if name == "read_page" and not self._bridge.started:
            return self._continuity_content
        if self.builtins and name in {tool["name"] for tool in BUILTIN_TOOLS}:
            return result_to_text(
                await call_builtin(
                    self._bridge,
                    name,
                    args,
                    self._sites,
                    self._repositories,
                    self._repository_manager,
                    self._profiles,
                    self._workflows,
                    self._adaptive,
                )
            )
        if name in self._site_mapping:
            return result_to_text(
                await self._bridge.call_tool(self._site_mapping[name], args)
            )
        raise ValueError(f'Unknown AnyBridge tool: "{name}".')

    # ---- framework adapters ----

    def langchain_tools(self) -> list:
        """LangChain / LangGraph tools, ready for create_react_agent(...).

        Requires `pip install anybridge[langchain]`.
        """
        from langchain_core.tools import StructuredTool

        tools = []
        for spec in self.tool_specs():
            tools.append(
                StructuredTool.from_function(
                    coroutine=self._coroutine_for(spec["name"]),
                    name=spec["name"],
                    description=spec["description"],
                    args_schema=_args_model(spec),
                )
            )
        return tools

    def openai_tools(self) -> list[dict]:
        """OpenAI-style function schemas; dispatch calls back through `call`."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["input_schema"],
                },
            }
            for spec in self.tool_specs()
        ]

    def anthropic_tools(self) -> list[dict]:
        """Claude API tool definitions; dispatch calls back through `call`."""
        return [
            {
                "name": spec["name"],
                "description": spec["description"],
                "input_schema": spec["input_schema"],
            }
            for spec in self.tool_specs()
        ]

    def _coroutine_for(self, name: str):
        async def run(**kwargs):
            return await self.call(name, kwargs)

        run.__name__ = name
        return run


_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _args_model(spec: dict):
    """Build a pydantic model for a tool's JSON Schema arguments."""
    from pydantic import Field, create_model

    schema = spec["input_schema"] or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields = {}
    for arg, definition in properties.items():
        annotation = _JSON_TYPES.get(definition.get("type"), str)
        description = definition.get("description", "")
        if arg in required:
            fields[arg] = (annotation, Field(description=description))
        else:
            fields[arg] = (annotation | None, Field(default=None, description=description))
    return create_model(f"{spec['name']}_args", **fields)
