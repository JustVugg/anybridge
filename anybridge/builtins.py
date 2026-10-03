"""Built-in tools for websites and Git repositories."""

import json
import importlib.util
from urllib.parse import urlsplit

from .browser import PageBridge
from .action_policy import assess_action
from .checkpoint import CheckpointStore, decide_resume, make_checkpoint
from .engines import AdaptiveReader
from .profiles import ProfileStore
from .repositories import PreparedRepository, RepositoryManager, RepositoryStore
from .sites import SiteStore
from .webmcp import publish_tools
from .tool_trust import ToolTrustRegistry
from .content_boundary import wrap_tool_output
from .webmcp_security import evaluate_webmcp_security
from .webmcp_security_evolution import DefenseObservation, SecurityEvolutionStore, evolve_security_capabilities, evaluate_and_evolve_security
from .webmcp_replay import SecurityReplayCase, SecurityReplayOutcome, replay_security_corpus
from .webmcp_defense_selection import select_defense
from .webmcp_capability_graduation import CapabilityGraduationStore
from .capability_transfer import CapabilityTransferStore, TransferObservation
from .capability_curriculum import CapabilityCurriculumStore, CurriculumCandidate, CurriculumObservation
from .capability_calibration import CapabilityCalibrationStore, CalibrationObservation
from .holdout_generation import HoldoutGenerator, BenchmarkObservation
from .holdout_benchmark import HoldoutBenchmark
from .capability_promotion import CanaryEvidence, CapabilityPromotionStore, CohortEvidence, HoldoutEvidence
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
        "name": "assess_action",
        "description": "Classify an action's risk and whether automatic retry, revalidation, confirmation, or denial is appropriate. Classification is advisory and does not execute the action.",
        "inputSchema": {"type": "object", "properties": {
            "action": {"type": "string"},
            "target": {"type": "string"},
            "consequential": {"type": "boolean"}
        }, "required": ["action"]},
    },
    {
        "name": "save_checkpoint",
        "description": "Save a crash-safe execution checkpoint. Checkpoints are resume boundaries, not permission to replay side effects.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Local checkpoint JSON path"},
            "execution_id": {"type": "string"},
            "checkpoint_id": {"type": "string"},
            "step_index": {"type": "integer"},
            "action": {"type": "string"},
            "target": {"type": "string"},
            "expected": {},
            "observed": {},
            "safe_to_resume": {"type": "boolean", "default": false}
        }, "required": ["path", "execution_id", "checkpoint_id", "step_index", "action"]},
    },
    {
        "name": "resume_checkpoint",
        "description": "Inspect a saved checkpoint and decide whether resume is safe after current-state revalidation.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"},
            "current_url": {"type": "string"},
            "current_target_exists": {"type": "boolean"},
            "recovery_confidence": {"type": "number"}
        }, "required": ["path"]},
    },
    {
        "name": "reset_session",
        "description": "Recover the AnyBridge browser after a crash or broken page.",
        "inputSchema": {"type": "object", "properties": {},},
    },
    {
        "name": "network_policy",
        "description": "Show private-site isolation, trusted hosts, and third-party dependency hosts blocked during this session. No network data is sent.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "revoke_network_host",
        "description": (
            "Revoke an explicitly trusted dependency hostname for the current browser session. "
            "The private-site origin itself cannot be revoked until the session ends."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Hostname previously trusted for this session"}
            },
            "required": ["host"],
        },
    },
        "name": "trust_network_host",
        "description": "Explicitly trust one blocked dependency hostname for the current browser session. Use only after the user approves that host.",
        "inputSchema": {
            "type": "object",
            "properties": {"host": {"type": "string", "description": "Hostname to trust, e.g. cdn.example.com"}},
            "required": ["host"],
        },
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
        "name": "tool_trust",
        "description": "Inspect WebMCP tool provenance, definition drift, safety annotations, and deterministic trust state without executing the tool.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "inspect_webmcp_tool",
        "description": "Assess one currently registered WebMCP tool for origin binding, schema/description drift, output trust, and confirmation requirements.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Raw or published WebMCP tool name"},
                "trust": {"type": "boolean", "default": false, "description": "Record explicit caller trust for an unchanged capability"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "webmcp_security_pipeline",
        "description": "Run AnyBridge deterministic WebMCP security evaluation through learning and capability evolution. Report only; generated cases are never executed.",
        "inputSchema": {"type": "object", "properties": {"provider": {"type": "string"}, "origin": {"type": "string"}, "defense": {"type": "string"}, "generate_counter_cases": {"type": "boolean", "default": true}}},
    },
    {
        "name": "webmcp_capability_graduation",
        "description": "Evaluate evidence-gated WebMCP security capability graduation state. Report only; no replay or tool execution.",
        "inputSchema": {"type": "object", "properties": {"observations": {"type": "array"}, "cohort": {"type": "string", "enum": ["evidence","holdout","canary"]}},"required":["observations","cohort"]},
    },
    {
        "name": "webmcp_defense_decision",
        "description": "Select and compare WebMCP defenses from caller-supplied security outcomes. Report only; no tool or attack execution.",
        "inputSchema": {"type": "object", "properties": {"observations": {"type": "array"}, "attack_class": {"type": "string"}, "provider": {"type": "string"}, "origin": {"type": "string"}, "schema_hash": {"type": "string"}},"required":["observations","attack_class"]},
    },
    {
        "name": "webmcp_security_replay",
        "description": "Process caller-supplied WebMCP replay outcomes through security learning and evolution.",
        "inputSchema": {"type": "object", "properties": {"cases": {"type": "array"}, "outcomes": {"type": "array"}, "generate_counter_cases": {"type": "boolean", "default": true}}},
    },
    {
        "name": "webmcp_security_evolution",
        "description": "Analyze caller-supplied WebMCP security outcomes to learn defense effectiveness, detect regressions, evolve bounded counter-cases, and manage regression-seed lifecycle. Does not execute generated cases.",
        "inputSchema": {"type": "object", "properties": {"observations": {"type": "array"}, "generate_counter_cases": {"type": "boolean", "default": true}}, "required": ["observations"]},
    },
    {
        "name": "capability_cross_domain_transfer",
        "description": "Abstract verified capabilities and evaluate deterministic cross-domain transfer hypotheses from caller-supplied outcomes. Report only; holdout and canary execution remain caller-owned.",
        "inputSchema": {"type": "object", "properties": {
            "primitives": {"type": "array"},
            "target_domain": {"type": "string"},
            "target_capability_class": {"type": "string"},
            "source_domain": {"type": "string"},
            "observations": {"type": "array"}
        }, "required": ["primitives", "target_domain", "target_capability_class"]},
    },
    {
        "name": "capability_curriculum",
        "description": "Rank and select the next capability-validation candidates from novelty, uncertainty, failure risk, evidence value, cost, transfer gaps, and realized outcomes. Report only; execution remains caller-owned.",
        "inputSchema": {"type": "object", "properties": {
            "candidates": {"type": "array"},
            "observations": {"type": "array"},
            "budget": {"type": "integer", "default": 3}
        }, "required": ["candidates"]},
    },
    {
        "name": "capability_confidence_calibration",
        "description": "Calibrate predicted capability confidence against caller-supplied holdout outcomes. Report only; no execution or authorization.",
        "inputSchema": {"type": "object", "properties": {"observations": {"type": "array"}, "capability_id": {"type": "string"}},"required":["observations"]},
    },
    {
        "name": "capability_holdout_generation",
        "description": "Generate bounded provenance-marked holdout cases from capability metadata and accept only independently scored outcomes. Generated cases are data, never executable instructions.",
        "inputSchema": {"type":"object","properties":{"capabilities":{"type":"array"},"target_domains":{"type":"array"},"observations":{"type":"array"}},"required":["capabilities","target_domains"]},
    },
    {
        "name": "capability_holdout_benchmark",
        "description": "Aggregate independently scored generated holdouts into benchmark evidence. Benchmark results never auto-promote capabilities.",
        "inputSchema": {"type":"object","properties":{"cases":{"type":"array"},"outcomes":{"type":"array"}},"required":["cases","outcomes"]},
    },
    {
        "name": "capability_promotion_decision",
        "description": "Evaluate caller-supplied independent holdout and canary evidence for capability promotion or rollback. Does not execute or authorize the capability.",
        "inputSchema": {"type":"object","properties":{"holdout":{"type":"object"},"cohorts":{"type":"array"},"benchmark_results":{"type":"array"},"canary":{"type":"array"}},"required":[]},
    },
    {
        "name": "webmcp_security_evaluation",
        "description": "Run the deterministic WebMCP adversarial regression corpus against AnyBridge quarantine, provenance, and action-confirmation boundaries. Does not execute page tools.",
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
        "name": "run_bdd",
        "description": "Execute a Gherkin feature file through the active browser provider and return scenario/step results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "feature": {"type": "string", "description": "Feature file path or complete Gherkin text"},
                "variables": {"type": "object"}
            },
            "required": ["feature"]
        }
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
    if name == "assess_action":
        result = assess_action(
            str(args["action"]),
            target=args.get("target"),
            consequential=args.get("consequential"),
        )
        return json.dumps(result.__dict__, indent=2, ensure_ascii=False)
    if name == "save_checkpoint":
        current = await bridge.current_site()
        checkpoint = make_checkpoint(
            execution_id=str(args["execution_id"]),
            checkpoint_id=str(args["checkpoint_id"]),
            step_index=int(args["step_index"]),
            action=str(args["action"]),
            target=args.get("target"),
            url=current.get("url"),
            expected=args.get("expected"),
            observed=args.get("observed"),
            evidence={"site": current},
            safe_to_resume=bool(args.get("safe_to_resume", False)),
        )
        CheckpointStore(str(args["path"])).save(checkpoint)
        return json.dumps(checkpoint.to_dict(), indent=2, ensure_ascii=False)
    if name == "resume_checkpoint":
        checkpoint = CheckpointStore(str(args["path"])).load()
        decision = decide_resume(
            checkpoint,
            current_url=args.get("current_url") or (await bridge.current_site()).get("url"),
            current_target_exists=args.get("current_target_exists"),
            recovery_confidence=float(args.get("recovery_confidence") or 0.0),
        )
        return json.dumps(decision.__dict__, indent=2, ensure_ascii=False)
    if name == "reset_session":
        return await bridge.reset()
    if name == "network_policy":
        return json.dumps(await bridge.network_policy(), indent=2, ensure_ascii=False)
    if name == "revoke_network_host":
        return json.dumps(await bridge.revoke_host(str(args.get("host") or "")), indent=2, ensure_ascii=False)

    if name == "trust_network_host":
        return json.dumps(await bridge.trust_host(args["host"]), indent=2, ensure_ascii=False)
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
    if name == "tool_trust":
        raw_tools = await bridge.discover_tools()
        current = await bridge.current_site()
        registry = getattr(bridge, "_tool_trust_registry", None)
        if registry is None:
            registry = ToolTrustRegistry()
            setattr(bridge, "_tool_trust_registry", registry)
        origin = current.get("url") or ""
        parsed = urlsplit(origin)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        assessments = []
        for raw in raw_tools:
            item = dict(raw)
            item["origin"] = item.get("origin") or origin
            assessments.append(registry.observe(item))
        return json.dumps([item.to_dict() for item in assessments], indent=2, ensure_ascii=False)
    if name == "inspect_webmcp_tool":
        raw_tools = await bridge.discover_tools()
        current = await bridge.current_site()
        registry = getattr(bridge, "_tool_trust_registry", None)
        if registry is None:
            registry = ToolTrustRegistry()
            setattr(bridge, "_tool_trust_registry", registry)
        origin = current.get("url") or ""
        parsed = urlsplit(origin)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        requested = str(args["name"])
        published, mapping = publish_tools(raw_tools, current.get("url"))
        original_name = mapping.get(requested, requested)
        match = next((dict(tool) for tool in raw_tools if str(tool.get("name") or "") == original_name), None)
        if match is None:
            raise ValueError(f'No WebMCP tool named "{requested}" is registered on this page.')
        match["origin"] = match.get("origin") or origin
        assessment = registry.observe(match, trusted=bool(args.get("trust")))
        return json.dumps(assessment.to_dict(), indent=2, ensure_ascii=False)
    if name == "webmcp_security_pipeline":
        report = evaluate_and_evolve_security(provider=args.get("provider", "playwright"), origin=args.get("origin", "https://trusted.example"), defense=args.get("defense", "content_boundary"), generate_counter_cases=bool(args.get("generate_counter_cases", True)))
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    if name == "webmcp_capability_graduation":
        store = CapabilityGraduationStore()
        cohort = str(args.get("cohort") or "evidence")
        observations = [DefenseObservation(str(x["attack_id"]), str(x["attack_class"]), str(x["provider"]), str(x["origin"]), str(x["schema_hash"]), str(x["defense"]), bool(x["blocked"]), float(x.get("evidence_confidence", 0.5)), x.get("latency_ms"), x.get("execution_id"), x.get("details")) for x in (args.get("observations") or []) if isinstance(x, dict)]
        decision = None
        for observation in observations: decision = store.observe(observation, cohort=cohort)
        return json.dumps((decision.to_dict() if decision else {"status": "candidate", "reason": "no observations"}), indent=2, ensure_ascii=False)
    if name == "webmcp_defense_decision":
        observations = [DefenseObservation(str(x["attack_id"]), str(x["attack_class"]), str(x["provider"]), str(x["origin"]), str(x["schema_hash"]), str(x["defense"]), bool(x["blocked"]), float(x.get("evidence_confidence", 0.5)), x.get("latency_ms"), x.get("execution_id"), x.get("details")) for x in (args.get("observations") or []) if isinstance(x, dict)]
        decision = select_defense(observations, attack_class=str(args["attack_class"]), provider=str(args.get("provider") or ""), origin=str(args.get("origin") or ""), schema_hash=str(args.get("schema_hash") or ""))
        return json.dumps(decision.to_dict(), indent=2, ensure_ascii=False)
    if name == "webmcp_security_replay":
        cases = [SecurityReplayCase(case_id=str(x.get("case_id") or x.get("id") or ""), attack_id=str(x.get("attack_id") or x.get("id") or ""), attack_class=str(x.get("attack_class") or x.get("category") or "unknown"), provider=str(x.get("provider") or "unknown"), origin=str(x.get("origin") or ""), schema_hash=str(x.get("schema_hash") or ""), defense=str(x.get("defense") or "unknown"), payload=x.get("payload") or {}) for x in (args.get("cases") or []) if isinstance(x, dict)]
        outcomes = [SecurityReplayOutcome(case_id=str(x.get("case_id") or ""), blocked=bool(x.get("blocked")), evidence_confidence=float(x.get("evidence_confidence", 0.5)), latency_ms=x.get("latency_ms"), execution_id=x.get("execution_id"), details=x.get("details")) for x in (args.get("outcomes") or []) if isinstance(x, dict)]
        report = replay_security_corpus(cases, outcomes, generate_counter_cases=bool(args.get("generate_counter_cases", True)))
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    if name == "webmcp_security_evolution":
        store = SecurityEvolutionStore()
        observations = []
        for item in args.get("observations") or []:
            if not isinstance(item, dict):
                raise ValueError("Each security evolution observation must be an object.")
            observations.append(DefenseObservation(
                attack_id=str(item["attack_id"]), attack_class=str(item["attack_class"]),
                provider=str(item["provider"]), origin=str(item["origin"]),
                schema_hash=str(item["schema_hash"]), defense=str(item["defense"]),
                blocked=bool(item["blocked"]), evidence_confidence=float(item.get("evidence_confidence", 0.5)),
                latency_ms=item.get("latency_ms"), execution_id=item.get("execution_id"), details=item.get("details"),
            ))
        report = evolve_security_capabilities(observations, store=store, generate_counter_cases=bool(args.get("generate_counter_cases", True)))
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    if name == "capability_cross_domain_transfer":
        from .capability_composition import CapabilityPrimitive
        store = CapabilityTransferStore()
        primitives = []
        for item in args.get("primitives") or []:
            if not isinstance(item, dict):
                raise ValueError("Each transfer primitive must be an object.")
            primitives.append(CapabilityPrimitive(
                str(item["capability_id"]), str(item["attack_class"]), str(item["defense"]),
                int(item.get("version", 1)), float(item.get("confidence", 0.0)),
                tuple(item.get("dependencies") or ()), tuple(item.get("compatible_with") or ()),
                tuple(item.get("incompatible_with") or ()),
            ))
        store.abstract_patterns(primitives, source_domain=str(args.get("source_domain") or "source"))
        hypotheses = store.propose(
            target_domain=str(args["target_domain"]),
            target_capability_class=str(args["target_capability_class"]),
            source_domain=args.get("source_domain"),
        )
        decisions = []
        for item in args.get("observations") or []:
            if not isinstance(item, dict):
                raise ValueError("Each transfer observation must be an object.")
            observation = TransferObservation(
                transfer_id=str(item["transfer_id"]),
                passed=bool(item["passed"]),
                holdout=bool(item.get("holdout", False)),
                evidence_confidence=float(item.get("evidence_confidence", 0.5)),
                source_domain=str(item.get("source_domain") or ""),
                target_domain=str(item.get("target_domain") or ""),
                execution_id=str(item.get("execution_id") or ""),
                details=str(item.get("details") or ""),
            )
            decisions.append(store.observe(observation).to_dict())
        return json.dumps({
            "hypotheses": [x.to_dict() for x in hypotheses],
            "decisions": decisions,
            "active": [x.to_dict() for x in store.active()],
        }, indent=2, ensure_ascii=False)
    if name == "capability_curriculum":
        store = CapabilityCurriculumStore()
        for item in args.get("candidates") or []:
            if not isinstance(item, dict):
                raise ValueError("Each curriculum candidate must be an object.")
            store.register(CurriculumCandidate(
                str(item["candidate_id"]), str(item["capability_id"]), str(item.get("domain") or ""),
                float(item.get("novelty", 0)), float(item.get("uncertainty", 0)),
                float(item.get("failure_risk", 0)), float(item.get("evidence_value", 0)),
                float(item.get("estimated_cost", 0)), float(item.get("transfer_gap", 0)),
            ))
        for item in args.get("observations") or []:
            if not isinstance(item, dict):
                raise ValueError("Each curriculum observation must be an object.")
            store.observe(CurriculumObservation(
                str(item["candidate_id"]), bool(item["passed"]), bool(item.get("holdout", False)),
                float(item.get("evidence_confidence", 0)), float(item.get("cost", 0)),
                float(item.get("duration_ms", 0)), str(item.get("details") or ""),
            ))
        decision = store.decide(budget=int(args.get("budget", 3)))
        return json.dumps(decision.to_dict(), indent=2, ensure_ascii=False)
    if name == "capability_confidence_calibration":
        store = CapabilityCalibrationStore()
        for item in args.get("observations") or []:
            if not isinstance(item, dict):
                raise ValueError("Each calibration observation must be an object.")
            store.observe(CalibrationObservation(
                str(item["capability_id"]), float(item["predicted_confidence"]),
                bool(item["passed"]), bool(item.get("holdout", True)),
                float(item.get("evidence_confidence", 0.0)),
            ))
        if args.get("capability_id"):
            return json.dumps(store.result(str(args["capability_id"])).to_dict(), indent=2, ensure_ascii=False)
        return json.dumps([x.to_dict() for x in store.results()], indent=2, ensure_ascii=False)
    if name == "capability_holdout_generation":
        from .capability_composition import CapabilityPrimitive
        generator = HoldoutGenerator()
        primitives = []
        for item in args.get("capabilities") or []:
            if not isinstance(item, dict):
                raise ValueError("Each capability must be an object.")
            primitives.append(CapabilityPrimitive(
                str(item["capability_id"]), str(item["attack_class"]), str(item["defense"]),
                int(item.get("version",1)), float(item.get("confidence",0)),
                tuple(item.get("dependencies") or ()), tuple(item.get("compatible_with") or ()),
                tuple(item.get("incompatible_with") or ()),
            ))
        cases = generator.generate(primitives, target_domains=args["target_domains"])
        for item in args.get("observations") or []:
            if not isinstance(item, dict):
                raise ValueError("Each holdout observation must be an object.")
            generator.observe(BenchmarkObservation(
                str(item["case_id"]), bool(item["passed"]),
                float(item.get("evidence_confidence",0)), bool(item.get("independent",True)),
                str(item.get("execution_id") or ""), str(item.get("details") or ""),
            ))
        return json.dumps(generator.export(), indent=2, ensure_ascii=False)
    if name == "capability_holdout_benchmark":
        from .holdout_generation import HoldoutCase, BenchmarkObservation
        benchmark = HoldoutBenchmark()
        cases=[]
        for item in args.get("cases") or []:
            if not isinstance(item,dict):
                raise ValueError("Each benchmark case must be an object.")
            cases.append(HoldoutCase(
                str(item["case_id"]),str(item["source_capability_id"]),str(item["target_domain"]),
                str(item["variation"]),float(item.get("difficulty",.5)),
                str(item.get("independence_key") or ""),bool(item.get("generated",True)),
                bool(item.get("executable",False)),str(item.get("provenance") or "unknown"),
            ))
        outcomes=[]
        for item in args.get("outcomes") or []:
            if not isinstance(item,dict):
                raise ValueError("Each benchmark outcome must be an object.")
            outcomes.append(BenchmarkObservation(
                str(item["case_id"]),bool(item["passed"]),float(item.get("evidence_confidence",0)),
                bool(item.get("independent",True)),str(item.get("execution_id") or ""),
                str(item.get("details") or ""),
            ))
        return json.dumps(benchmark.export() | {"results":[x.to_dict() for x in benchmark.ingest(cases,outcomes)]},indent=2,ensure_ascii=False)
    if name == "capability_learning_fabric":
        from .holdout_benchmark import BenchmarkResult
        fabric = CapabilityLearningFabric(curriculum_budget=int(args.get("budget", 3)))
        for item in args.get("candidates") or []:
            if not isinstance(item, dict):
                raise ValueError("Each curriculum candidate must be an object.")
            fabric.register_candidates([CurriculumCandidate(
                str(item["candidate_id"]), str(item["capability_id"]), str(item.get("domain") or ""),
                float(item.get("novelty", 0)), float(item.get("uncertainty", 0)),
                float(item.get("failure_risk", 0)), float(item.get("evidence_value", 0)),
                float(item.get("estimated_cost", 0)), float(item.get("transfer_gap", 0)),
            )])
        outcomes = []
        for item in args.get("outcomes") or []:
            if not isinstance(item, dict):
                raise ValueError("Each capability outcome must be an object.")
            outcomes.append(CapabilityOutcome(
                capability_id=str(item["capability_id"]),
                predicted_confidence=float(item["predicted_confidence"]),
                passed=bool(item["passed"]),
                evidence_confidence=float(item.get("evidence_confidence", 0.0)),
                holdout=bool(item.get("holdout", False)),
                independent=bool(item.get("independent", False)),
                domain=str(item.get("domain") or ""),
                provider=str(item.get("provider") or ""),
                cohort_id=str(item.get("cohort_id") or ""),
                benchmark_family=str(item.get("benchmark_family") or ""),
                independence_key=str(item.get("independence_key") or ""),
                candidate_id=str(item.get("candidate_id") or ""),
                execution_id=str(item.get("execution_id") or ""),
                details=str(item.get("details") or ""),
            ))
        benchmark_results = []
        for item in args.get("benchmark_results") or []:
            if not isinstance(item, dict):
                raise ValueError("Each benchmark result must be an object.")
            benchmark_results.append(BenchmarkResult(
                str(item["capability_id"]), str(item["domain"]),
                int(item["attempts"]), float(item["pass_rate"]),
                float(item.get("confidence", 0.0)),
                int(item.get("independent_attempts", item["attempts"])),
                str(item["status"]), str(item.get("reason", "")),
                str(item.get("cohort_id") or item.get("independence_key") or ""),
                str(item.get("independence_key") or item.get("cohort_id") or ""),
                str(item.get("benchmark_family") or "generated_holdout"),
            ))
        report = fabric.evaluate(outcomes, benchmark_results=benchmark_results)
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)

    if name == "capability_outcome_attribution":
        calibration = CapabilityCalibrationStore()
        curriculum = CapabilityCurriculumStore()
        for item in args.get("candidates") or []:
            if not isinstance(item, dict):
                raise ValueError("Each curriculum candidate must be an object.")
            curriculum.register(CurriculumCandidate(
                str(item["candidate_id"]), str(item["capability_id"]), str(item.get("domain") or ""),
                float(item.get("novelty", 0)), float(item.get("uncertainty", 0)),
                float(item.get("failure_risk", 0)), float(item.get("evidence_value", 0)),
                float(item.get("estimated_cost", 0)), float(item.get("transfer_gap", 0)),
            ))
        for item in args.get("calibration_history") or []:
            if not isinstance(item, dict):
                raise ValueError("Each calibration history item must be an object.")
            calibration.observe(CalibrationObservation(
                str(item["capability_id"]), float(item["predicted_confidence"]),
                bool(item["passed"]), bool(item.get("holdout", False)),
                float(item.get("evidence_confidence", 0.0)), bool(item.get("eligible", True)),
            ))
        outcomes = []
        for item in args.get("outcomes") or []:
            if not isinstance(item, dict):
                raise ValueError("Each capability outcome must be an object.")
            outcomes.append(CapabilityOutcome(
                capability_id=str(item["capability_id"]),
                predicted_confidence=float(item["predicted_confidence"]),
                passed=bool(item["passed"]),
                evidence_confidence=float(item.get("evidence_confidence", 0.0)),
                holdout=bool(item.get("holdout", False)),
                independent=bool(item.get("independent", False)),
                domain=str(item.get("domain") or ""),
                provider=str(item.get("provider") or ""),
                cohort_id=str(item.get("cohort_id") or ""),
                benchmark_family=str(item.get("benchmark_family") or ""),
                independence_key=str(item.get("independence_key") or ""),
                candidate_id=str(item.get("candidate_id") or ""),
                execution_id=str(item.get("execution_id") or ""),
                details=str(item.get("details") or ""),
            ))
        store = CapabilityOutcomeAttributionStore()
        report = store.report(outcomes, calibration_store=calibration, curriculum_store=curriculum)
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    if name == "capability_promotion_decision":
        holdout = args.get("holdout") or {}
        if not isinstance(holdout, dict):
            raise ValueError("holdout must be an object.")
        store = CapabilityPromotionStore()
        benchmark_items = args.get("benchmark_results") or []
        if benchmark_items:
            from .holdout_benchmark import BenchmarkResult
            results = []
            for item in benchmark_items:
                if not isinstance(item, dict):
                    raise ValueError("Each benchmark result must be an object.")
                results.append(BenchmarkResult(
                    str(item["capability_id"]), str(item["domain"]),
                    int(item["attempts"]), float(item["pass_rate"]),
                    float(item.get("confidence", 0.0)),
                    int(item.get("independent_attempts", item["attempts"])),
                    str(item["status"]), str(item.get("reason", "")),
                    str(item.get("cohort_id") or item.get("independence_key") or ""),
                    str(item.get("independence_key") or item.get("cohort_id") or ""),
                    str(item.get("benchmark_family") or "generated_holdout"),
                ))
            decision = store.evaluate_benchmark_results(results)
        else:
            cohort_items = args.get("cohorts") or []
        if cohort_items:
            observations = []
            for item in cohort_items:
                if not isinstance(item, dict):
                    raise ValueError("Each cohort observation must be an object.")
                observations.append(CohortEvidence(
                    str(item["capability_id"]), str(item["cohort_id"]), bool(item["passed"]),
                    float(item.get("confidence", item.get("evidence_confidence", 0.0))),
                    str(item.get("domain") or ""), str(item.get("benchmark_family") or ""),
                    str(item.get("independence_key") or ""),
                ))
            decision = store.evaluate_multi_cohort(observations)
        else:
            decision = store.evaluate_holdout(HoldoutEvidence(
                str(holdout["capability_id"]),
                int(holdout["attempts"]),
                float(holdout["pass_rate"]),
                float(holdout.get("confidence", 0.0)),
                bool(holdout.get("independent", True)),
            ))
        if decision.state.status == "canary":
            observations = []
            for item in args.get("canary") or []:
                if not isinstance(item, dict):
                    raise ValueError("Each canary observation must be an object.")
                observations.append(CanaryEvidence(
                    str(item.get("capability_id") or holdout["capability_id"]),
                    bool(item["passed"]),
                    float(item.get("evidence_confidence", 0.0)),
                ))
            if observations:
                decision = store.evaluate_canary(str(holdout["capability_id"]), observations)
        return json.dumps(decision.to_dict(), indent=2, ensure_ascii=False)
    if name == "webmcp_security_evaluation":
        return json.dumps(evaluate_webmcp_security().to_dict(), indent=2, ensure_ascii=False)
    if name == "call_webmcp_tool":
        tool_name = args["name"]
        raw_tools = await bridge.discover_tools()
        current = await bridge.current_site()
        _, mapping = publish_tools(raw_tools, current.get("url"))
        available = {tool["name"] for tool in raw_tools}
        original_name = mapping.get(tool_name, tool_name)
        selected = next((dict(tool) for tool in raw_tools if str(tool.get("name") or "") == original_name), None)
        if selected is None or original_name not in available:
            raise ValueError(f'No WebMCP tool named "{tool_name}" is registered on this page.')
        registry = getattr(bridge, "_tool_trust_registry", None)
        if registry is None:
            registry = ToolTrustRegistry()
            setattr(bridge, "_tool_trust_registry", registry)
        parsed = urlsplit(current.get("url") or "")
        selected["origin"] = selected.get("origin") or (
            f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        )
        assessment = registry.observe(selected)
        if assessment.requires_confirmation:
            raise PermissionError(
                f'WebMCP tool "{tool_name}" requires confirmation/revalidation: '
                + "; ".join(assessment.reasons)
            )
        try:
            result = await bridge.call_tool(original_name, args.get("arguments") or {})
        except Exception as exc:
            registry.record_outcome(assessment.origin, assessment.name, False)
            envelope = wrap_tool_output(
                origin=assessment.origin,
                name=assessment.name,
                value=str(exc),
                untrusted=True,
            )
            return json.dumps(envelope.to_dict(), ensure_ascii=False)
        registry.record_outcome(assessment.origin, assessment.name, True)
        envelope = wrap_tool_output(
            origin=assessment.origin,
            name=assessment.name,
            value=result,
            untrusted=assessment.output_untrusted,
            high_risk=assessment.risk.value == "consequential",
        )
        return json.dumps(envelope.to_dict(), ensure_ascii=False)
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
    if name == "run_bdd":
        from .bdd import BDDRunner, parse_feature
        value = str(args["feature"])
        try:
            from pathlib import Path
            p = Path(value)
            source = p.read_text(encoding="utf-8") if p.exists() else value
        except OSError:
            source = value
        return json.dumps(
            await BDDRunner(bridge).run(parse_feature(source), args.get("variables") or {}),
            indent=2,
            ensure_ascii=False,
        )

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
