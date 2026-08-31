"""Built-in tools that work on any website, WebMCP or not."""

import json

from .browser import PageBridge

BUILTIN_TOOLS = [
    {
        "name": "read_page",
        "description": (
            "Read the current page as markdown. Use a CSS selector to scope to one "
            "part of the page, and max_chars to read more of a long page."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Optional CSS selector to scope the extraction"},
                "max_chars": {"type": "integer", "description": "Maximum characters returned (default 20000)"},
            },
        },
    },
    {
        "name": "navigate",
        "description": "Open a URL in the browser and return the page content as markdown.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Absolute URL to open"}},
            "required": ["url"],
        },
    },
    {
        "name": "list_links",
        "description": "List links on the current page (text and URL), optionally filtered by a substring.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Only links whose text or URL contains this"},
                "limit": {"type": "integer", "description": "Maximum links returned (default 100)"},
            },
        },
    },
    {
        "name": "list_forms",
        "description": "List the forms on the current page with their fields, so submit_form can be called.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_form",
        "description": (
            "Fill and submit a form by its index from list_forms; returns the resulting page as markdown."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "form": {"type": "integer", "description": "Form index from list_forms"},
                "fields": {
                    "type": "object",
                    "description": "Field name to value, e.g. {\"q\": \"search terms\"}",
                },
            },
            "required": ["form", "fields"],
        },
    },
    {
        "name": "type_text",
        "description": (
            "Type into an input found by placeholder, label, or CSS selector — works even for "
            "inputs outside a <form> (single-page apps). Optionally press Enter to submit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Placeholder text, label, or CSS selector"},
                "text": {"type": "string", "description": "Text to type"},
                "press_enter": {"type": "boolean", "description": "Press Enter after typing (default false)"},
            },
            "required": ["target", "text"],
        },
    },
    {
        "name": "click",
        "description": (
            "Click a link or button by its visible text (or a CSS selector) and return the resulting page. "
            "Useful for navigation, cookie banners, and 'load more' buttons."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"target": {"type": "string", "description": "Visible text or CSS selector"}},
            "required": ["target"],
        },
    },
]

BUILTIN_NAMES = {t["name"] for t in BUILTIN_TOOLS}


async def call_builtin(bridge: PageBridge, name: str, args: dict) -> str:
    args = args or {}
    if name == "read_page":
        return await bridge.read_page(args.get("selector"), args.get("max_chars") or 20000)
    if name == "navigate":
        return await bridge.navigate(args["url"])
    if name == "list_links":
        links = await bridge.list_links(args.get("filter"), args.get("limit") or 100)
        if not links:
            return "No links found."
        return "\n".join(f"- [{l['text']}]({l['url']})" for l in links)
    if name == "list_forms":
        forms = await bridge.list_forms()
        if not forms:
            return "No forms on this page."
        return json.dumps(forms, indent=2, ensure_ascii=False)
    if name == "submit_form":
        return await bridge.submit_form(int(args["form"]), args.get("fields") or {})
    if name == "type_text":
        return await bridge.type_text(
            args["target"], args["text"], bool(args.get("press_enter"))
        )
    if name == "click":
        return await bridge.click(args["target"])
    raise ValueError(f"Unknown builtin tool: {name}")
