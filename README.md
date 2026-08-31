# anybridge

**Expose any web page's WebMCP tools to any agent.**

[WebMCP](https://github.com/webmachinelearning/webmcp) lets a website register typed, callable tools for AI agents (`navigator.modelContext.registerTool` / `document.modelContext`). It is in origin trial in Chrome — but today only Gemini in Chrome can consume those tools.

anybridge closes that gap: it opens the page, captures every WebMCP tool the site registers, and re-exposes them as a **standard MCP server**. Anything that speaks MCP — Claude, the OpenAI Agents SDK, any MCP client — can now drive the site through the tools the site itself declared. Tool calls run inside the real page, so the site's own JavaScript does the work.

```
              site JS registers tools
  web page ──────────────────────────► anybridge shim ──► MCP server ──► any agent
           ◄────────────────────────── tool calls proxied back into the page
```

## Install

```bash
pip install anybridge          # or: pip install -e . from a clone
playwright install chromium
```

## Usage

List the tools a page registers:

```bash
anybridge list https://example.com
```

Call one directly:

```bash
anybridge call https://example.com add_task --args '{"title": "ship v1"}'
```

Serve the page as an MCP server (stdio):

```bash
anybridge serve https://example.com
```

### Works with any agent

anybridge speaks plain MCP over stdio, so any MCP client can drive the site — Claude, GPT, open-source frameworks, your own agent loop.

**Claude Code:**

```bash
claude mcp add mysite -- anybridge serve https://example.com
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mysite": {
      "command": "anybridge",
      "args": ["serve", "https://example.com"]
    }
  }
}
```

**GPT via OpenAI Agents SDK:**

```python
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

async with MCPServerStdio(
    params={"command": "anybridge", "args": ["serve", "https://example.com"]}
) as site:
    agent = Agent(name="assistant", mcp_servers=[site])
    result = await Runner.run(agent, "Add a task called 'ship v1'")
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

1. A shim is injected before any page script runs, defining `navigator.modelContext` and `document.modelContext` (and wrapping the native ones where they exist).
2. Every `registerTool` / `provideContext` call lands in a registry inside the page.
3. The MCP server mirrors that registry as MCP tools; each `call_tool` is evaluated inside the page, invoking the site's own `execute` function.

## Roadmap

- Streamable HTTP transport (one bridge, many agents)
- Sessions with login state (persistent browser profiles)
- Fallback for sites **without** WebMCP: derive tools from forms and page actions
- Tool change notifications (pages that register tools dynamically per view)

## License

MIT
