# anybridge

**Expose any web page to any agent.**

anybridge opens a page in a real browser and re-exposes it as a **standard MCP server**, so anything that speaks MCP — Claude, GPT via the OpenAI Agents SDK, any MCP client — can drive the site. Tools come from two sources, merged:

1. **WebMCP tools the site registers.** [WebMCP](https://github.com/webmachinelearning/webmcp) lets a website declare typed, callable tools for AI agents (`navigator.modelContext.registerTool` / `document.modelContext`). It is in origin trial in Chrome — but today only Gemini in Chrome can consume those tools. anybridge captures them and hands them to every other agent.
2. **Built-in universal tools** that work on *any* site, WebMCP or not: `read_page`, `navigate`, `list_links`, `list_forms`, `submit_form`, `type_text`, `click`.

Every call runs inside the real page — the site's own JavaScript does the work.

```
              site JS registers WebMCP tools
  web page ─────────────────────────────────► anybridge ──► MCP server ──► any agent
           ◄───────────────────────────────── tool calls executed in the live page
              + universal read/navigate/forms/click on any site
```

## Give it to an agent

One line, nothing to install — [uv](https://docs.astral.sh/uv/) fetches anybridge, and anybridge downloads its browser on first run:

**Claude Code**

```bash
claude mcp add mysite -- uvx anybridge serve https://example.com
```

**Claude Desktop** (`claude_desktop_config.json`) — and the same shape works for any MCP client:

```json
{
  "mcpServers": {
    "mysite": {
      "command": "uvx",
      "args": ["anybridge", "serve", "https://example.com"]
    }
  }
}
```

**GPT / OpenAI Agents SDK**

```python
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

async with MCPServerStdio(
    params={"command": "uvx", "args": ["anybridge", "serve", "https://example.com"]}
) as site:
    agent = Agent(name="assistant", mcp_servers=[site])
    result = await Runner.run(agent, "What does this site sell, and what are the prices?")
```

That is the whole setup. The agent now has `read_page`, `navigate`, `list_links`, `list_forms`, `submit_form`, `type_text` and `click` on that site, plus any WebMCP tools the site registers.

## Install

Only needed to use the CLI directly:

```bash
pip install anybridge     # or: pip install -e . from a clone
```

Chromium downloads itself on first run. On a bare Linux box its system libraries may be missing — `sudo playwright install-deps chromium` installs them.

## Usage

List the tools available on a page (the site's WebMCP tools plus the built-ins):

```bash
anybridge list https://example.com
```

Call tools directly from the shell — works on real sites today:

```bash
# read any page as markdown
anybridge call https://en.wikipedia.org/wiki/Rome read_page

# search Wikipedia by filling its real search form
anybridge call https://en.wikipedia.org/wiki/Main_Page submit_form \
  --args '{"form": 0, "fields": {"search": "Model Context Protocol"}}'

# type into a React SPA input (no <form> needed) and press Enter
anybridge call https://demo.playwright.dev/todomvc/ type_text \
  --args '{"target": "What needs to be done?", "text": "hello", "press_enter": true}'

# call a WebMCP tool the site registered
anybridge call https://example.com add_task --args '{"title": "ship v1"}'
```

Serve the page as an MCP server (stdio):

```bash
anybridge serve https://example.com          # WebMCP tools + built-ins
anybridge serve https://example.com --no-builtins   # only the site's own tools
```

### Use it from Python

```python
from anybridge import PageBridge

async with PageBridge("https://example.com") as bridge:
    tools = await bridge.wait_for_tools()
    result = await bridge.call_tool("add_task", {"title": "ship v1"})
```

## Try the demo

`examples/demo-site/index.html` is a task-list page that registers three WebMCP tools:

```bash
anybridge list "file://$PWD/examples/demo-site/index.html"
anybridge call "file://$PWD/examples/demo-site/index.html" add_task --args '{"title": "hello"}'
```

## How it works

1. Playwright launches full Chromium in new-headless mode (less likely to be blocked as a bot), with automatic retries on transient network errors.
2. Two scripts are injected before any page script runs: a shim defining `navigator.modelContext` / `document.modelContext` (wrapping the native ones where they exist), and the page-tools helpers for DOM extraction and form handling.
3. Every `registerTool` / `provideContext` call lands in a registry inside the page. The MCP server mirrors that registry, merged with the built-in tools (site names win collisions).
4. Each tool call is evaluated inside the live page: WebMCP calls invoke the site's own `execute` function; built-ins read the DOM, fill real form fields (with native setters, so React apps see the input), click real elements, and follow navigations and new tabs.

## Limits

Sites behind aggressive anti-bot walls (CAPTCHA, some search engines' bot checks) may still refuse a headless browser — anybridge reports what the page actually served, so the agent sees the refusal instead of a hallucination. Cookie banners are handled by the agent itself with `click("Accept all")`.

## Roadmap

- Streamable HTTP transport (one bridge, many agents)
- Sessions with login state (persistent browser profiles)
- Tool change notifications (pages that register tools dynamically per view)
- Browserless fast path for static pages

## License

MIT
