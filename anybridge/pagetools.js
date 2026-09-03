// anybridge page tools — DOM extraction and form handling, injected in every document.
(() => {
  if (window.__anybridge_pt__) return;

  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "IFRAME", "svg", "SVG"]);
  const BLOCK = new Set([
    "DIV", "SECTION", "ARTICLE", "MAIN", "HEADER", "FOOTER", "NAV", "ASIDE",
    "FORM", "FIELDSET", "FIGURE", "FIGCAPTION", "DL", "DT", "DD",
  ]);

  const style = (el) => {
    try {
      return getComputedStyle(el);
    } catch {
      return null;
    }
  };

  const hidden = (el) => {
    const s = style(el);
    return !!s && (s.display === "none" || s.visibility === "hidden");
  };

  const toMd = (node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      // Text inherits visibility from its parent (which children can override,
      // so a hidden ancestor must not prune the walk — see below).
      const p = node.parentElement;
      if (p) {
        const s = style(p);
        if (s && s.visibility === "hidden") return "";
      }
      return node.textContent.replace(/\s+/g, " ");
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const tag = node.tagName;
    if (SKIP.has(tag)) return "";
    const s = style(node);
    // display:none hides the whole subtree — safe to prune.
    // visibility:hidden is NOT: descendants can set visibility:visible (Wix does).
    if (s && s.display === "none") return "";
    if (s && s.visibility === "hidden" && tag === "IMG") return "";
    const kids = [...node.childNodes].map(toMd).join("");
    const t = () => kids.trim();
    switch (tag) {
      case "H1": return `\n\n# ${t()}\n\n`;
      case "H2": return `\n\n## ${t()}\n\n`;
      case "H3": return `\n\n### ${t()}\n\n`;
      case "H4":
      case "H5":
      case "H6": return `\n\n#### ${t()}\n\n`;
      case "P": return `\n\n${t()}\n\n`;
      case "BR": return "\n";
      case "HR": return "\n\n---\n\n";
      case "LI": return t() ? `\n- ${t()}` : "";
      case "UL":
      case "OL": return `\n${kids}\n`;
      case "BLOCKQUOTE": return `\n\n> ${t()}\n\n`;
      case "A": {
        const raw = node.getAttribute("href");
        const label = t();
        if (!label) return "";
        if (!raw || raw.startsWith("javascript:") || raw.startsWith("#")) return label;
        return `[${label}](${node.href})`;
      }
      case "STRONG":
      case "B": return t() ? `**${t()}**` : "";
      case "EM":
      case "I": return t() ? `*${t()}*` : "";
      case "CODE":
        return node.parentElement && node.parentElement.tagName === "PRE"
          ? kids
          : "`" + t() + "`";
      case "PRE": return `\n\n\`\`\`\n${node.textContent}\n\`\`\`\n\n`;
      case "TR": {
        const cells = [...node.children].map((c) => toMd(c).trim().replace(/\|/g, "\\|"));
        return `\n| ${cells.join(" | ")} |`;
      }
      case "TABLE": return `\n${kids}\n`;
      case "IMG": {
        const alt = node.getAttribute("alt");
        return alt ? `[img: ${alt}]` : "";
      }
      case "INPUT":
      case "BUTTON":
      case "SELECT":
      case "TEXTAREA": return ""; // form controls are exposed via list_forms
      default: return BLOCK.has(tag) ? `\n${kids}\n` : kids;
    }
  };

  const clean = (s) => s.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();

  const labelFor = (el) => {
    let lbl = "";
    if (el.id) {
      const found = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (found) lbl = found.textContent.trim();
    }
    if (!lbl) {
      const wrap = el.closest("label");
      if (wrap) lbl = wrap.textContent.trim();
    }
    return (
      lbl ||
      el.getAttribute("placeholder") ||
      el.getAttribute("aria-label") ||
      el.getAttribute("title") ||
      ""
    ).replace(/\s+/g, " ").slice(0, 120);
  };

  const setNative = (el, value) => {
    const proto =
      el.tagName === "TEXTAREA"
        ? HTMLTextAreaElement.prototype
        : el.tagName === "SELECT"
          ? HTMLSelectElement.prototype
          : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };

  // A compact semantic view for agents. Refs deliberately live in the page so
  // every MCP client uses the same deterministic target instead of guessing a
  // CSS selector or screen coordinate.
  const refState = { id: 0, refs: new Map() };

  const visible = (el) => {
    if (!el || !el.isConnected) return false;
    const s = style(el);
    if (s && (s.display === "none" || s.visibility === "hidden")) return false;
    if (el.hasAttribute("hidden") || el.getAttribute("aria-hidden") === "true") return false;
    return true;
  };

  const roleFor = (el) => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit.split(/\s+/)[0];
    const tag = el.tagName;
    if (/^H[1-6]$/.test(tag)) return "heading";
    if (tag === "A" && el.hasAttribute("href")) return "link";
    if (tag === "BUTTON") return "button";
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "SELECT") return "combobox";
    if (tag === "IMG") return "img";
    if (tag === "FORM") return "form";
    if (tag === "NAV") return "navigation";
    if (tag === "MAIN") return "main";
    if (tag === "INPUT") {
      const type = (el.type || "text").toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "range") return "slider";
      if (["button", "submit", "reset", "image"].includes(type)) return "button";
      return type === "search" ? "searchbox" : "textbox";
    }
    return explicit || "";
  };

  const nameFor = (el) => {
    let value =
      el.getAttribute("aria-label") ||
      (el.getAttribute("aria-labelledby") || "")
        .split(/\s+/)
        .filter(Boolean)
        .map((id) => document.getElementById(id)?.textContent || "")
        .join(" ") ||
      labelFor(el) ||
      el.getAttribute("alt") ||
      el.getAttribute("placeholder") ||
      el.getAttribute("title") ||
      el.getAttribute("value") ||
      el.innerText ||
      el.textContent ||
      "";
    return String(value).replace(/\s+/g, " ").trim().slice(0, 180);
  };

  const interactive = (el, role) =>
    ["button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "slider", "menuitem", "tab", "switch"].includes(role) ||
    el.hasAttribute("contenteditable") ||
    el.tabIndex >= 0 ||
    typeof el.onclick === "function";

  const semanticSnapshot = (options = {}) => {
    const interactiveOnly = options.interactiveOnly !== false;
    const compact = options.compact !== false;
    const maxChars = Math.max(500, Math.min(Number(options.maxChars) || 12000, 50000));
    const selected = options.selector ? document.querySelector(options.selector) : document.body;
    if (!selected) throw new Error(`No element matches selector: ${options.selector}`);
    refState.id += 1;
    refState.refs.clear();
    let next = 0;
    const lines = [];
    // A "link farm" is a container holding a large menu (mega-navigation,
    // sitemaps, SharePoint quick-launch) and nothing else interactive. Every
    // link still gets a ref, but only the first few are printed: on portals the
    // same 150-entry menu would otherwise dominate every single snapshot.
    // Applies to any compact snapshot; compact=false is the explicit way out.
    const FARM_MIN_LINKS = 30;
    const FARM_SHOWN = 8;
    const isLinkFarm = (el) =>
      compact && el !== selected &&
      el.getElementsByTagName("a").length >= FARM_MIN_LINKS &&
      !el.querySelector("input, textarea, select, button, h1, h2, h3, h4, h5, h6, [role=main], main");
    const visit = (el, depth = 0, frame = "", farm = null) => {
      // Elements from a same-origin iframe belong to a different JS realm, so
      // `instanceof Element` would incorrectly reject them.
      if (!el || el.nodeType !== 1 || !visible(el) || SKIP.has(el.tagName)) return;
      const role = roleFor(el);
      const isInteractive = interactive(el, role);
      const isStructural = role || /^H[1-6]$/.test(el.tagName);
      const name = nameFor(el);
      let ownFarm = null;
      if (!farm && el.children.length && isLinkFarm(el)) {
        ownFarm = farm = { shown: 0, hidden: 0, first: "", last: "" };
      }
      if (isInteractive || (!interactiveOnly && isStructural && name)) {
        let ref = "";
        if (isInteractive) {
          ref = `e${++next}`;
          refState.refs.set(ref, el);
        }
        const level = /^H[1-6]$/.test(el.tagName) ? ` level=${el.tagName.slice(1)}` : "";
        const state = [
          el.disabled ? "disabled" : "",
          el.checked === true ? "checked" : "",
          el.getAttribute("aria-expanded") === "true" ? "expanded" : "",
        ].filter(Boolean).join(" ");
        const line = `${"  ".repeat(Math.min(depth, 6))}${ref ? `@${ref} ` : ""}[${role || el.tagName.toLowerCase()}]${name ? ` ${JSON.stringify(name)}` : ""}${level}${state ? ` ${state}` : ""}${frame}`;
        if (farm && role === "link" && farm.shown >= FARM_SHOWN) {
          farm.hidden += 1;
          farm.first = farm.first || ref;
          farm.last = ref;
        } else {
          if (farm && role === "link") farm.shown += 1;
          lines.push(line);
        }
      } else if (!compact && !el.children.length && name) {
        lines.push(`${"  ".repeat(Math.min(depth, 6))}[text] ${JSON.stringify(name)}`);
      }
      const children = [...el.children];
      if (el.shadowRoot) children.push(...el.shadowRoot.children);
      for (const child of children) visit(child, depth + (isStructural ? 1 : 0), frame, farm);
      if (ownFarm && ownFarm.hidden) {
        lines.push(
          `${"  ".repeat(Math.min(depth + 1, 6))}[... ${ownFarm.hidden} more menu links, @${ownFarm.first}–@${ownFarm.last}: click by text, or list_links / snapshot with a selector to see them]`
        );
      }
      if (el.tagName === "IFRAME") {
        try {
          if (el.contentDocument?.body) visit(el.contentDocument.body, depth + 1, " iframe");
        } catch {
          lines.push(`${"  ".repeat(Math.min(depth + 1, 6))}[iframe] cross-origin`);
        }
      }
    };
    visit(selected);
    const header = `snapshot=${refState.id} url=${location.href} title=${JSON.stringify(document.title || "")}`;
    let result = [header, ...lines].join("\n");
    if (result.length > maxChars) result = result.slice(0, maxChars) + "\n[... snapshot truncated]";
    return result;
  };

  const resolveRef = (ref) => {
    const key = String(ref || "").replace(/^@/, "");
    const el = refState.refs.get(key);
    if (!el || !el.isConnected) {
      throw new Error(`Unknown or stale element ref "${ref}". Take a new snapshot.`);
    }
    return el;
  };

  const extractValue = (root, definition) => {
    if (Array.isArray(definition)) {
      const item = definition[0];
      if (!item) return [];
      const selector = typeof item === "string" ? item : item.selector;
      if (!selector) return [];
      return [...root.querySelectorAll(selector)].map((match) => {
        if (typeof item === "string") return (match.innerText || match.textContent || "").trim();
        if (item.fields) {
          return Object.fromEntries(
            Object.entries(item.fields).map(([key, value]) => [key, extractValue(match, value)])
          );
        }
        return item.attr ? match.getAttribute(item.attr) : (match.innerText || match.textContent || "").trim();
      });
    }
    const spec = typeof definition === "string" ? { selector: definition } : definition || {};
    const match = spec.selector ? root.querySelector(spec.selector) : root;
    if (!match) return null;
    if (spec.fields) {
      return Object.fromEntries(
        Object.entries(spec.fields).map(([key, value]) => [key, extractValue(match, value)])
      );
    }
    return spec.attr ? match.getAttribute(spec.attr) : (match.innerText || match.textContent || "").trim();
  };

  window.__anybridge_pt__ = {
    extract(selector, maxChars) {
      maxChars = maxChars || 20000;
      const root = selector ? document.querySelector(selector) : document.body;
      if (!root)
        return selector ? `No element matches selector: ${selector}` : "__anybridge_no_body__";
      let md = clean(toMd(root));
      const head = `# ${document.title || "(no title)"}\nURL: ${location.href}\n\n`;
      if (!md && selector)
        return (
          head +
          `(The element matching "${selector}" has no readable content — it may be an empty ` +
          "scroll anchor. Try read_page without a selector, or a broader one.)"
        );
      if (md.length > maxChars) {
        md = md.slice(0, maxChars) + `\n\n[... truncated, ${md.length} chars total — call read_page with a selector or higher max_chars]`;
      }
      return head + md;
    },

    links(filter, limit) {
      limit = limit || 100;
      const needle = (filter || "").toLowerCase();
      const seen = new Set();
      const out = [];
      for (const a of document.querySelectorAll("a[href]")) {
        const s = style(a);
        if (s && s.display === "none") continue;
        // innerText is visibility-aware, so links hidden by animation wrappers
        // with visible children still surface, and truly hidden ones read empty.
        const text = (a.innerText || a.getAttribute("aria-label") || "")
          .replace(/\s+/g, " ").trim().slice(0, 150);
        const href = a.href;
        if (!text || !href || href.startsWith("javascript:")) continue;
        if (needle && !text.toLowerCase().includes(needle) && !href.toLowerCase().includes(needle))
          continue;
        const key = text + "|" + href;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ text, url: href });
        if (out.length >= limit) break;
      }
      return out;
    },

    forms() {
      return [...document.querySelectorAll("form")].map((form, index) => {
        const fields = [...form.elements]
          .filter((el) => ["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName))
          .filter((el) => (el.name || el.id) && !["hidden", "submit", "button", "image", "reset"].includes(el.type))
          .map((el) => {
            const f = {
              name: el.name || el.id,
              type: el.tagName === "SELECT" ? "select" : el.type || "text",
              label: labelFor(el),
              required: !!el.required,
            };
            if (el.tagName === "SELECT")
              f.options = [...el.options].map((o) => o.value || o.text).slice(0, 50);
            if (el.type !== "password" && el.value) f.value = String(el.value).slice(0, 100);
            return f;
          });
        // getAttribute, not property access: inputs named id/action/method
        // shadow the form's native properties (Shopify has <input name="id">).
        let action = form.getAttribute("action") || "";
        try {
          action = action ? new URL(action, location.href).href : location.href;
        } catch {}
        return {
          index,
          id: form.getAttribute("id") || "",
          name: form.getAttribute("name") || "",
          method: (form.getAttribute("method") || "get").toUpperCase(),
          action,
          fields,
        };
      });
    },

    snapshot(options) {
      return semanticSnapshot(options || {});
    },

    resolveRef(ref) {
      return resolveRef(ref);
    },

    describeRef(ref) {
      const el = resolveRef(ref);
      const parts = [];
      let node = el;
      while (node && node.nodeType === Node.ELEMENT_NODE && node !== document.body) {
        let part = node.tagName.toLowerCase();
        if (node.id) {
          part += `#${CSS.escape(node.id)}`;
          parts.unshift(part);
          break;
        }
        const parent = node.parentElement;
        if (parent) {
          const peers = [...parent.children].filter((candidate) => candidate.tagName === node.tagName);
          if (peers.length > 1) part += `:nth-of-type(${peers.indexOf(node) + 1})`;
        }
        parts.unshift(part);
        node = parent;
      }
      return { role: roleFor(el), name: nameFor(el), selector: parts.join(" > ") };
    },

    clickRef(ref) {
      resolveRef(ref).click();
    },

    fillRef(ref, value) {
      const el = resolveRef(ref);
      if (!["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) && !el.isContentEditable)
        throw new Error(`Element ref "${ref}" is not editable.`);
      if (el.isContentEditable) {
        el.textContent = String(value);
        el.dispatchEvent(new Event("input", { bubbles: true }));
      } else {
        setNative(el, String(value));
      }
    },

    selectRef(ref, value) {
      const el = resolveRef(ref);
      if (el.tagName !== "SELECT") throw new Error(`Element ref "${ref}" is not a select.`);
      setNative(el, String(value));
    },

    pressRef(ref, key) {
      const el = ref ? resolveRef(ref) : document.activeElement || document.body;
      el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true }));
      if (key === "Enter" && el.form) el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
    },

    structuredExtract(schema, selector) {
      const root = selector ? document.querySelector(selector) : document;
      if (!root) throw new Error(`No element matches selector: ${selector}`);
      return Object.fromEntries(
        Object.entries(schema || {}).map(([key, value]) => [key, extractValue(root, value)])
      );
    },

    fill(index, fields) {
      const form = document.querySelectorAll("form")[index];
      if (!form) throw new Error(`No form with index ${index} (see list_forms)`);
      for (const [name, value] of Object.entries(fields || {})) {
        const el = form.elements.namedItem(name);
        if (!el) throw new Error(`Form ${index} has no field "${name}"`);
        if (el instanceof RadioNodeList) {
          el.value = String(value);
          continue;
        }
        if (el.type === "checkbox") {
          el.checked = !(value === false || value === "false" || value === "" || value === 0);
          el.dispatchEvent(new Event("change", { bubbles: true }));
          continue;
        }
        setNative(el, String(value));
      }
    },

    submit(index) {
      const form = document.querySelectorAll("form")[index];
      if (!form) throw new Error(`No form with index ${index}`);
      const btn = form.querySelector(
        'button[type="submit"], input[type="submit"], button:not([type])'
      );
      if (form.requestSubmit) {
        btn ? form.requestSubmit(btn) : form.requestSubmit();
      } else if (btn) {
        btn.click();
      } else {
        form.submit();
      }
    },
  };
})();
