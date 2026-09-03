// anybridge shim — injected before any page script runs.
// Captures WebMCP tool registrations (navigator.modelContext / document.modelContext)
// into a single registry that the bridge can inspect and invoke from outside.
(() => {
  if (window.__anybridge__) return;

  const registry = new Map();

  window.__anybridge__ = {
    tools: registry,
    listTools() {
      return [...registry.values()].map((t) => ({
        name: t.name,
        description: t.description || "",
        inputSchema: t.inputSchema || { type: "object", properties: {} },
      }));
    },
    async callTool(name, args) {
      const tool = registry.get(name);
      if (!tool) throw new Error("Unknown tool: " + name);
      return await tool.execute(args ?? {});
    },
  };

  const makeContext = (native) => {
    const registerTool = (tool) => {
      if (!tool || !tool.name || typeof tool.execute !== "function") {
        throw new TypeError(
          "registerTool expects {name, description, inputSchema, execute}"
        );
      }
      registry.set(tool.name, tool);
      // Pass through so a browser with real WebMCP support keeps working.
      try {
        native?.registerTool?.(tool);
      } catch {}
      return {
        unregister: () => registry.delete(tool.name),
      };
    };

    return {
      registerTool,
      provideContext(ctx) {
        registry.clear();
        (ctx?.tools || []).forEach(registerTool);
        try {
          native?.provideContext?.(ctx);
        } catch {}
      },
    };
  };

  // The spec moved between navigator.modelContext and document.modelContext;
  // shim both so pages targeting either era register into the same registry.
  for (const host of [window.navigator, window.document]) {
    let native;
    try {
      native = host.modelContext;
    } catch {}
    try {
      Object.defineProperty(host, "modelContext", {
        value: makeContext(native),
        configurable: true,
      });
    } catch {}
  }
})();
