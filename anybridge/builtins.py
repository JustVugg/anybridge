"""Built-in tools for websites and Git repositories."""

import json
import importlib.util
from urllib.parse import urlsplit

from .browser import PageBridge
from .engines import AdaptiveReader
from .profiles import ProfileStore
from .repositories import PreparedRepository, RepositoryManager, RepositoryStore
from .sites import SiteStore
from .webmcp import publish_tools
from .workflows import WorkflowStore

BUILTIN_TOOLS = [
    {
        "name": "read_page",
        "description": (
            "Read the current page as markdown. Use a CSS selector to scope to one "
            "part of the page, and max_chars to read more of a long page. If the "
            "browser is showing a PDF, returns its extracted text with page markers; "
            'use pages="12-15" to read a specific range of a long document.'
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Optional CSS selector to scope the extraction"},
                "max_chars": {"type": "integer", "description": "Maximum characters returned (default 20000)"},
                "pages": {"type": "string", "description": 'PDF only: page or range, e.g. "7" or "12-15"'},
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
    {
        "name": "smart_read",
        "description": (
            "Read a URL through AnyBridge's continuity engine. It uses durable cache, "
            "HTTP, Lightpanda or Chromium, and Wayback as a historical last resort. "
            "For PDF documents it returns extracted, page-marked text (never screenshots): "
            'use pages="12-15" to read a specific range of a long document.'
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "prefer": {
                    "type": "string",
                    "enum": ["auto", "http", "lightpanda", "chromium", "archive"],
                },
                "max_chars": {"type": "integer", "default": 20000},
                "pages": {"type": "string", "description": 'PDF only: page or range, e.g. "7" or "12-15"'},
            },
            "required": ["url"],
        },
    },
    {
        "name": "engine_status",
        "description": "Show the adaptive engines available in this AnyBridge installation.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "snapshot",
        "description": (
            "Return a compact semantic snapshot. Interactive elements receive refs such as e3; "
            "prefer ref tools over text or CSS selectors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "interactive_only": {"type": "boolean", "default": True},
                "compact": {"type": "boolean", "default": True},
                "selector": {"type": "string"},
                "max_chars": {"type": "integer", "default": 12000},
            },
        },
    },
    {
        "name": "click_ref",
        "description": "Click an exact element ref from the latest snapshot and return fresh page state.",
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string", "description": "Element ref, e.g. e3"}},
            "required": ["ref"],
        },
    },
    {
        "name": "fill_ref",
        "description": "Fill an exact input ref without exposing the value in any saved workflow.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "value": {"type": "string"},
                "press_enter": {"type": "boolean", "default": False},
            },
            "required": ["ref", "value"],
        },
    },
    {
        "name": "select_ref",
        "description": "Choose a value in an exact select-element ref.",
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}, "value": {"type": "string"}},
            "required": ["ref", "value"],
        },
    },
    {
        "name": "press_key",
        "description": "Press a keyboard key, optionally on an element ref.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "ref": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "wait_for",
        "description": "Wait for visible text or a CSS selector, then return a fresh snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "timeout_ms": {"type": "integer", "default": 10000},
            },
        },
    },
    {
        "name": "extract",
        "description": (
            "Extract structured page data using a JSON object whose values are CSS selectors, "
            "{selector, attr} definitions, nested fields, or one-item arrays for repeated rows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"schema": {"type": "object"}, "selector": {"type": "string"}},
            "required": ["schema"],
        },
    },
    {
        "name": "screenshot",
        "description": "Capture the current page as PNG when semantic content is insufficient.",
        "inputSchema": {
            "type": "object",
            "properties": {"full_page": {"type": "boolean", "default": False}},
        },
    },
    {
        "name": "reset_session",
        "description": "Recover the AnyBridge browser after a crash or broken page.",
        "inputSchema": {"type": "object", "properties": {},},
    },
    {
        "name": "current_site",
        "description": "Show the current browser URL, page title, and number of discovered WebMCP tools.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_webmcp_tools",
        "description": (
            "List the WebMCP tools registered by the current website. Use this after navigation "
            "if newly discovered site tools are not already visible to you."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "call_webmcp_tool",
        "description": (
            "Call a WebMCP tool registered by the current website by name. Prefer the site's "
            "direct MCP tool when it is visible; this is the compatibility fallback."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "WebMCP tool name"},
                "arguments": {
                    "type": "object",
                    "description": "Arguments matching the WebMCP tool's input schema",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_saved_sites",
        "description": (
            "List website aliases saved in AnyBridge. Use this when the user refers to a site "
            "by a familiar name instead of giving its URL."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_site",
        "description": (
            "Save a website URL under a memorable name for future AnyBridge sessions and agents. "
            "If url is omitted, save the current page."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Memorable site name or alias"},
                "url": {
                    "type": "string",
                    "description": "URL to save; omit to use the current browser page",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "open_saved_site",
        "description": "Open a site previously saved in AnyBridge by its name or alias.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Saved site name or alias"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "remove_saved_site",
        "description": "Remove a saved site alias from AnyBridge.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Saved site name or alias"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_profiles",
        "description": "List encrypted browser profiles in the AnyBridge wallet (never returns cookies).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_profile",
        "description": (
            "Explicitly save the current login cookies and web storage encrypted in the wallet. "
            "Only call when the user asks AnyBridge to remember this session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "open_profile",
        "description": "Open an encrypted browser profile in read-only mode; changes are not persisted automatically.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "url": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "remove_profile",
        "description": "Permanently remove an encrypted browser profile from the wallet.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "observe",
        "description": "Inspect exact available page actions and WebMCP capabilities before acting.",
        "inputSchema": {
            "type": "object",
            "properties": {"intent": {"type": "string"}},
            "required": ["intent"],
        },
    },
    {
        "name": "start_workflow_recording",
        "description": "Start recording subsequent ref-based actions as a reusable deterministic workflow.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_workflow",
        "description": "Stop recording and save actions. Filled values become variables and are never stored.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "run_workflow",
        "description": "Replay a saved workflow using supplied variables, then return fresh page state.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "variables": {"type": "object"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_workflows",
        "description": "List reusable workflows and required variable names.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "remove_workflow",
        "description": "Remove a saved workflow without affecting sites or profiles.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_saved_repositories",
        "description": (
            "List Git repository aliases saved in AnyBridge. Use this when the user "
            "refers to a repository by a familiar name instead of giving its URL."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_repository",
        "description": (
            "Save a Git repository URL under a memorable name for future AnyBridge "
            "sessions. This records the alias without cloning the repository."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Repository name or alias"},
                "url": {
                    "type": "string",
                    "description": "GitHub, GitLab, HTTP(S), SSH, or git remote URL",
                },
            },
            "required": ["name", "url"],
        },
    },
    {
        "name": "open_repository",
        "description": (
            "Clone or reopen a Git repository in AnyBridge's shared repository directory. "
            "After this returns, use your native file, search, terminal, and code tools on "
            "the absolute path it provides. Optionally save the URL under a name."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "GitHub, GitLab, HTTP(S), SSH, or git remote URL",
                },
                "name": {
                    "type": "string",
                    "description": "Optional alias to save for future sessions",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "open_saved_repository",
        "description": (
            "Clone or reopen a repository previously saved in AnyBridge. Then use your "
            "native code tools on the absolute local path returned by this tool."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Saved repository alias"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "remove_saved_repository",
        "description": (
            "Remove a saved repository alias from AnyBridge. This never deletes its "
            "local clone or repository files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Saved repository alias"},
            },
            "required": ["name"],
        },
    },
]

BUILTIN_NAMES = {t["name"] for t in BUILTIN_TOOLS}


async def call_builtin(
    bridge: PageBridge,
    name: str,
    args: dict,
    sites: SiteStore | None = None,
    repositories: RepositoryStore | None = None,
    repository_manager: RepositoryManager | None = None,
    profiles: ProfileStore | None = None,
    workflows: WorkflowStore | None = None,
    adaptive: AdaptiveReader | None = None,
) -> object:
    args = args or {}
    sites = sites or SiteStore()
    repositories = repositories or RepositoryStore()
    repository_manager = repository_manager or RepositoryManager()
    profiles = profiles or ProfileStore()
    workflows = workflows or WorkflowStore()
    adaptive = adaptive or AdaptiveReader()
    if name == "read_page":
        return await bridge.read_page(
            args.get("selector"), args.get("max_chars") or 20000, pages=args.get("pages")
        )
    if name == "navigate":
        return await adaptive.navigate(args["url"], bridge=bridge)
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
    if name == "smart_read":
        result = await adaptive.read(
            args["url"],
            bridge=bridge,
            max_chars=args.get("max_chars") or 20000,
            prefer=args.get("prefer") or "auto",
            pages=args.get("pages"),
        )
        return result.as_text()
    if name == "engine_status":
        return json.dumps(
            {
                "http": True,
                "lightpanda": importlib.util.find_spec("lightpanda") is not None,
                "chromium": True,
                "persistent_cache": True,
                "wayback": True,
                "routing": "fresh cache -> HTTP -> Lightpanda -> Chromium -> stale cache -> Wayback",
                "continuity": "bounded calls; read-only fallback never claims a live action succeeded",
            },
            indent=2,
        )
    if name == "snapshot":
        return await bridge.snapshot(
            interactive_only=args.get("interactive_only", True),
            compact=args.get("compact", True),
            selector=args.get("selector"),
            max_chars=args.get("max_chars") or 12000,
        )
    if name == "click_ref":
        return await bridge.click_ref(args["ref"])
    if name == "fill_ref":
        return await bridge.fill_ref(
            args["ref"], args["value"], press_enter=bool(args.get("press_enter"))
        )
    if name == "select_ref":
        return await bridge.select_ref(args["ref"], args["value"])
    if name == "press_key":
        return await bridge.press_key(args["key"], args.get("ref"))
    if name == "wait_for":
        return await bridge.wait_for(
            selector=args.get("selector"),
            text=args.get("text"),
            timeout_ms=args.get("timeout_ms") or 10000,
        )
    if name == "extract":
        return json.dumps(
            await bridge.extract_structured(args["schema"], args.get("selector")),
            indent=2,
            ensure_ascii=False,
        )
    if name == "screenshot":
        return await bridge.screenshot(full_page=bool(args.get("full_page")))
    if name == "reset_session":
        return await bridge.reset()
    if name == "current_site":
        return json.dumps(await bridge.current_site(), indent=2, ensure_ascii=False)
    if name == "list_webmcp_tools":
        raw_tools = await bridge.discover_tools()
        current = await bridge.current_site()
        tools, _ = publish_tools(raw_tools, current.get("url"))
        if not tools:
            return (
                "The website has not registered native WebMCP tools. "
                "The AnyBridge-generated MCP bridge is active: use snapshot/ref tools "
                "for pages and smart_read for PDF documents."
            )
        return json.dumps(tools, indent=2, ensure_ascii=False)
    if name == "call_webmcp_tool":
        tool_name = args["name"]
        raw_tools = await bridge.discover_tools()
        current = await bridge.current_site()
        _, mapping = publish_tools(raw_tools, current.get("url"))
        available = {tool["name"] for tool in raw_tools}
        original_name = mapping.get(tool_name, tool_name)
        if original_name not in available:
            raise ValueError(f'No WebMCP tool named "{tool_name}" is registered on this page.')
        result = await bridge.call_tool(original_name, args.get("arguments") or {})
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    if name == "list_saved_sites":
        saved = sites.list()
        if not saved:
            return "No sites have been saved in AnyBridge yet."
        return json.dumps(
            [{"name": site.name, "url": site.url} for site in saved],
            indent=2,
            ensure_ascii=False,
        )
    if name == "save_site":
        url = args.get("url")
        if not url:
            current = await bridge.current_site()
            url = current.get("url")
            if not url:
                raise ValueError("Open a website before saving the current page.")
        saved = sites.save(args["name"], url)
        return f'Saved "{saved.name}" as {saved.url}.'
    if name == "open_saved_site":
        saved = sites.get(args["name"])
        content = await adaptive.navigate(saved.url, bridge=bridge)
        return f'Opened saved site "{saved.name}" ({saved.url}).\n\n{content}'
    if name == "remove_saved_site":
        removed = sites.remove(args["name"])
        return f'Removed saved site "{removed.name}".'
    if name == "list_profiles":
        entries = profiles.list()
        return json.dumps(entries, indent=2, ensure_ascii=False) if entries else "No browser profiles saved."
    if name == "save_profile":
        current = await bridge.current_site()
        url = current.get("url")
        if not url:
            raise ValueError("Open a website before saving a browser profile.")
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        saved = profiles.save(
            args["name"], origin, await bridge.storage_snapshot(origin=origin)
        )
        return f'Saved encrypted profile "{saved.name}" for {saved.origin}.'
    if name == "open_profile":
        profile = profiles.get(args["name"])
        target = args.get("url") or profile.origin
        parsed_target = urlsplit(target)
        target_origin = f"{parsed_target.scheme}://{parsed_target.netloc}"
        if target_origin.casefold() != profile.origin.casefold():
            raise ValueError(
                f'Profile "{profile.name}" is scoped to {profile.origin}; '
                "open it only on that origin."
            )
        state = await bridge.load_storage_snapshot(profile.state, target)
        return f'Opened profile "{profile.name}" in read-only mode.\n\n{state}'
    if name == "remove_profile":
        removed = profiles.remove(args["name"])
        return f'Removed encrypted profile "{removed["name"]}".'
    if name == "observe":
        return json.dumps(
            {
                "intent": args["intent"],
                "snapshot": await bridge.snapshot(interactive_only=True, compact=True),
                "webmcp_tools": await bridge.list_tools(),
                "instruction": "Use exact refs or a namespaced WebMCP tool. Validate before consequential actions.",
            },
            indent=2,
            ensure_ascii=False,
        )
    if name == "start_workflow_recording":
        bridge.begin_recording()
        return "Workflow recording started. Use ref-based actions, then call save_workflow."
    if name == "save_workflow":
        steps = bridge.end_recording()
        start_url = bridge.recording_start_url
        if not start_url:
            raise ValueError("Open a page before recording a workflow.")
        parsed = urlsplit(start_url)
        saved = workflows.save(
            args["name"], f"{parsed.scheme}://{parsed.netloc}", start_url, steps
        )
        return json.dumps(
            {"name": saved.name, "origin": saved.origin, "steps": len(saved.steps), "variables": saved.variables},
            indent=2,
            ensure_ascii=False,
        )
    if name == "run_workflow":
        workflow = workflows.get(args["name"])
        await bridge.navigate(workflow.start_url)
        variables = args.get("variables") or {}
        for step in workflow.steps:
            await bridge.run_recorded_step(step, variables)
        return await bridge.snapshot(interactive_only=True, compact=True)
    if name == "list_workflows":
        saved = workflows.list()
        if not saved:
            return "No workflows saved."
        return json.dumps(
            [
                {"name": item.name, "origin": item.origin, "steps": len(item.steps), "variables": item.variables}
                for item in saved
            ],
            indent=2,
            ensure_ascii=False,
        )
    if name == "remove_workflow":
        removed = workflows.remove(args["name"])
        return f'Removed workflow "{removed.name}".'
    if name == "list_saved_repositories":
        saved = repositories.list()
        if not saved:
            return "No repositories have been saved in AnyBridge yet."
        return json.dumps(
            [{"name": repository.name, "url": repository.url} for repository in saved],
            indent=2,
            ensure_ascii=False,
        )
    if name == "save_repository":
        saved = repositories.save(args["name"], args["url"])
        return f'Saved repository "{saved.name}" as {saved.url}.'
    if name == "open_repository":
        prepared = await repository_manager.prepare_async(args["url"])
        alias = None
        if args.get("name"):
            alias = repositories.save(args["name"], args["url"]).name
        return _prepared_repository_result(prepared, alias=alias)
    if name == "open_saved_repository":
        saved = repositories.get(args["name"])
        prepared = await repository_manager.prepare_async(saved.url)
        return _prepared_repository_result(prepared, alias=saved.name)
    if name == "remove_saved_repository":
        removed = repositories.remove(args["name"])
        return (
            f'Removed saved repository "{removed.name}". '
            "Any existing local clone was left untouched."
        )
    raise ValueError(f"Unknown builtin tool: {name}")


def _prepared_repository_result(
    prepared: PreparedRepository,
    alias: str | None = None,
) -> str:
    payload = {
        "url": prepared.url,
        "path": str(prepared.path),
        "cloned": prepared.cloned,
        "instruction": (
            "Continue with native file, search, terminal, and code tools in this path."
        ),
    }
    if alias:
        payload["name"] = alias
    return json.dumps(payload, indent=2, ensure_ascii=False)
