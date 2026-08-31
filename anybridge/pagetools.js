// anybridge page tools — DOM extraction and form handling, injected in every document.
(() => {
  if (window.__anybridge_pt__) return;

  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "IFRAME", "svg", "SVG"]);
  const BLOCK = new Set([
    "DIV", "SECTION", "ARTICLE", "MAIN", "HEADER", "FOOTER", "NAV", "ASIDE",
    "FORM", "FIELDSET", "FIGURE", "FIGCAPTION", "DL", "DT", "DD",
  ]);

  const hidden = (el) => {
    try {
      const s = getComputedStyle(el);
      return s.display === "none" || s.visibility === "hidden";
    } catch {
      return false;
    }
  };

  const toMd = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent.replace(/\s+/g, " ");
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const tag = node.tagName;
    if (SKIP.has(tag) || hidden(node)) return "";
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
      case "LI": return `\n- ${t()}`;
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

  window.__anybridge_pt__ = {
    extract(selector, maxChars) {
      maxChars = maxChars || 20000;
      const root = selector ? document.querySelector(selector) : document.body;
      if (!root)
        return selector ? `No element matches selector: ${selector}` : "__anybridge_no_body__";
      let md = clean(toMd(root));
      const head = `# ${document.title || "(no title)"}\nURL: ${location.href}\n\n`;
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
        if (hidden(a)) continue;
        const text = a.textContent.replace(/\s+/g, " ").trim().slice(0, 150);
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
        return {
          index,
          id: form.id || "",
          name: form.getAttribute("name") || "",
          method: (form.method || "get").toUpperCase(),
          action: form.action || "",
          fields,
        };
      });
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
