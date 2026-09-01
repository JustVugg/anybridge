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
        self._bridge = PageBridge(url, headless=headless)
        self._site_tools: list[dict] = []

    async def open(self, wait: float = 3.0) -> "BridgeSession":
        await self._bridge.start()
        # Give a WebMCP page a moment to register its tools; sites without it
        # simply return an empty list and the built-ins carry the session.
        self._site_tools = await self._bridge.wait_for_tools(timeout=wait)
        return self

    async def close(self):
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
        site_names = {t["name"] for t in self._site_tools}
        tools = list(self._site_tools)
        if self.builtins:
            tools += [t for t in BUILTIN_TOOLS if t["name"] not in site_names]
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
        self._site_tools = await self._bridge.list_tools()

    async def call(self, name: str, args: dict | None = None) -> str:
        """Run one tool in the live page and return its result as text."""
        args = args or {}
        if name in {t["name"] for t in self._site_tools}:
            return result_to_text(await self._bridge.call_tool(name, args))
        return result_to_text(await call_builtin(self._bridge, name, args))

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
