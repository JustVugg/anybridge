# anybridge

**Any website, within reach of any agent.**

<p align="center">
  <img src="docs/tui.png" alt="The anybridge TUI: pick an agent and launch it" width="760">
</p>

anybridge turns a website into tools an AI agent can actually use. It opens the
page in a real browser and re-exposes it over the **Model Context Protocol**, so
Claude Code, Codex, or anything else that speaks MCP can read the page, fill its
forms, click its buttons and read its PDFs — including sites built long before
agents existed.

Run it, pick your agent, paste a URL into it. That is the whole flow.

## Why it exists

[WebMCP](https://github.com/webmachinelearning/webmcp) lets a website declare
typed, callable tools for AI agents. It is real and already deployed — Shopify
ships it on storefronts today — but only Gemini in Chrome can consume those
tools. anybridge captures them and hands them to **every other agent**.

And when a site has no WebMCP at all, which is almost every site, anybridge
generates the bridge itself from the live DOM. A 2013 SharePoint portal and a
modern Shopify store come out the same way: as a list of tools.

## What an agent gets

Two sources of tools, merged, with the site's own taking precedence:

**The site's native WebMCP tools**, namespaced so a page can never shadow a
built-in — `search_catalog`, `update_cart`, whatever the site registered.

**Forty universal tools that work on any page.** The ones that matter most:

| | |
|---|---|
| `navigate`, `read_page`, `smart_read` | open and read, with PDFs returned as page-marked text |
| `snapshot` | a compact semantic tree with stable refs (`@e12`) instead of raw HTML |
| `click_ref`, `fill_ref`, `select_ref`, `press_key` | act on those refs |
| `extract`, `screenshot`, `wait_for` | structured data, images, synchronisation |
| `save_site`, `save_profile`, `save_workflow` | remember a site, a login, a recorded flow |
| `open_repository` | clone a Git remote and hand back a local path |

## Run it

One command, nothing installed. [uv](https://docs.astral.sh/uv/) fetches
anybridge and its Python for you, and anybridge downloads its browser on first
run:

```bash
uvx --from git+https://github.com/JustVugg/anybridge anybridge
```

That opens the TUI: pick your agent, and anybridge launches it in a second
terminal already wired to the bridge.

To keep it around, install it properly (Python 3.10 or newer):

```bash
git clone https://github.com/JustVugg/anybridge.git
cd anybridge
pip install -e .
```

On a bare Linux box `sudo playwright install-deps chromium` supplies Chromium's
system libraries. Not on PyPI yet.

## Use it

**Wire it into an MCP client:**

```bash
claude mcp add anybridge -- anybridge serve
```

```json
{
  "mcpServers": {
    "anybridge": { "command": "anybridge", "args": ["serve"] }
  }
}
```

**Or from the shell, with no agent at all:**

```bash
anybridge list https://www.bauzaar.it/     # the site's WebMCP tools plus the built-ins
anybridge call https://en.wikipedia.org/wiki/Rome read_page
```

**Or as a library**, when you want tools your framework already understands:

```python
from anybridge import BridgeSession
from langgraph.prebuilt import create_react_agent

async with BridgeSession("https://www.bauzaar.it/") as site:
    agent = create_react_agent("anthropic:claude-sonnet-5", site.langchain_tools())
```

`site.anthropic_tools()`, `site.openai_tools()` and `site.tool_specs()` cover the
other frameworks. One browser backs the whole session, so calls share page state.

## How it holds up

**It picks the cheapest route that works.** Plain HTTP first, then optional
Lightpanda, and Chromium only when the page truly needs JavaScript or state.

**It stays available.** Every route is bounded by one shared deadline. If the
live site fails, anybridge degrades to a durable cache and finally to the
Internet Archive — and says so, instead of pretending an action succeeded.

**PDFs are text, never screenshots.** Chromium's PDF viewer exposes an empty
DOM, so anybridge extracts the document, marks every page, and lets an agent
ask for `pages="12-15"` of a long one.

**Untrusted pages stay in their box.** Remote sessions get their own browser,
private-network targets are blocked including across redirects, saved profiles
are encrypted and scoped to one origin, and recorded workflows never store the
values that were typed into them.

### Blocked dependency discovery

When a private-site page tries to reach an untrusted dependency, AnyBridge blocks
the request and records the hostname locally. Agents can call `network_policy`
to inspect the blocked dependency list without sending page data anywhere.

After the user approves a specific hostname, call `trust_network_host` with that
hostname. The approval applies only to the current browser session. If approval
is no longer needed, call `revoke_network_host` to remove it without restarting
the browser.

For example:

1. Open the private QA site.
2. Call `network_policy`.
3. Review the blocked hostname.
4. Ask the user whether that specific CDN, authentication, analytics, or telemetry
   service is trusted.
5. Only after approval, call `trust_network_host`.
6. Retry the page operation.

The policy records only the blocked hostname, scheme, and a generic policy reason;
it does not retain the blocked request path, query string, fragment, or response
body. It does not contact the blocked service to discover more information.

### Private-site dependencies

Strict private-site isolation does not disable the browser. The site's own
origin remains available, while unrelated hosts are blocked. If the application
needs an API, authentication service, CDN, analytics, telemetry, or another
third-party dependency, the user can explicitly trust that host.

Use `--allow-host` to grant access for the current AnyBridge process. Repeat it
for multiple dependencies; exact hosts and trusted subdomain wildcards are
supported:

```bash
anybridge serve https://qa.example.internal \
  --allow-host cdn.example.com \
  --allow-host auth.example.com \
  --allow-host analytics.example.com
```

A wildcard is useful when you trust the complete dependency domain:

```bash
anybridge serve https://xxx.planfuldev.com --allow-host "*.planfuldev.com"
```

The equivalent environment setting is useful for MCP launchers that do not
make CLI arguments convenient:

```bash
ANYBRIDGE_PRIVATE_ALLOWED_HOSTS="cdn.example.com,auth.example.com" anybridge serve https://qa.example.internal
```

These are explicit opt-ins, not automatic exceptions. Without an allow rule,
third-party requests remain blocked after private-site isolation begins. Use
wildcards only when you trust the entire domain, and be especially careful
with analytics and telemetry because those services may receive page or
browser data according to the site's own behavior.
### Private-session fallback and browser isolation

Private-site sessions use the same network policy for the browser and the
HTTP reader. A public host visited before private isolation is not silently
promoted to trusted status when the session later enters private mode.

When private isolation is active, historical Internet Archive fallback is
disabled. Otherwise a continuity fallback could disclose the private target
hostname to a third-party archive service. Public sessions may still use the
archive fallback when enabled.

AnyBridge also blocks Service Worker registration in the browser context used
for network isolation. This keeps browserContext routing as the primary
network-control boundary for page requests.

### Resource-aware counterfactual planning

`decide_execution_adaptive()` extends the fabric with optional resource signals:
context budget, estimated duration, CPU/memory availability, queue depth,
concurrency capacity, and evidence value. Resource pressure can reduce concurrency,
but never overrides destructive/high-risk serial execution.

The function also returns safe counterfactual plans such as serial instead of
parallel execution, deeper verification, or alternate-provider escalation. These
are alternatives for HWS to evaluate against realized outcomes; AnyBridge does not
execute them or persist the learning decision.

```python
from anybridge.adaptive_fabric import ResourceObservation, decide_execution_adaptive

decision, alternatives = decide_execution_adaptive(
    required=["bdd"],
    independent_work=4,
    resources=ResourceObservation(
        cpu_available=0.75,
        memory_available_mb=4096,
        queue_depth=1,
        concurrency_capacity=4,
        evidence_value=0.8,
    ),
)
```

`decision_telemetry()` emits a stable JSON-friendly record containing the chosen
plan and resource signals so HWS can merge it into its canonical Evidence Envelope.


### Failure-aware decomposition

`recommend_decomposition()` turns execution telemetry into advisory decomposition
guidance: smaller verified partitions after repeated failures, network-dependency
isolation after network failures, and staged execution with explicit preconditions
for high-risk or destructive work. Independent low-risk work remains parallel.

The recommendation is declarative. HWS decides whether and how to execute it, and
can feed the realized outcome back into the next decision.

```python
from anybridge.decomposition import recommend_decomposition

decision = recommend_decomposition(
    independent_work=6,
    history=[...],
    max_parallelism=4,
)
```

The result includes strategy, suggested parallelism, verification depth,
preconditions, escalation and a confidence/reason trace. `decomposition_telemetry()`
provides a stable record for the HWS Evidence Graph.

## Limits

Aggressive anti-bot walls can still refuse a headless browser. When that
happens anybridge reports what the page actually served and continues read-only
from another source — it never invents a completed purchase, login or form
submission. Archived pages are historical and cannot be used for live actions.

## Development

```bash
python -m pytest tests/                                              # 49 tests
ANYBRIDGE_SKIP_BROWSER_TESTS=1 python -m unittest discover -s tests   # without a browser
```

## License

MIT

## Browser providers and BDD

AnyBridge keeps browser semantics provider-neutral. Playwright remains the default,
and Selenium/WebDriver is available as an optional provider:

- `pip install anybridge[selenium]`
- `anybridge serve https://internal.example --provider selenium --browser chrome`
- `anybridge bdd features/login.feature --provider selenium --browser chrome`

Supported Selenium browsers in the provider are Chrome, Edge, and Firefox. Selenium
4.49+ is used so WebDriver BiDi can provide request interception where the browser
supports it. The same AnyBridge network policy is applied to top-level navigation
and BiDi subresource requests when BiDi is available.

BDD is provider-neutral. The `run_bdd` MCP tool and `anybridge bdd` command execute
Gherkin Features, Backgrounds, Scenarios, Scenario Outlines, Examples, tags, and
common Given/When/Then/And/But actions. Custom step definitions can be layered on
top of the BDDRunner without coupling them to Playwright or Selenium.

The optional `anybridge[bdd]` extra installs pytest-bdd for projects that also want
pytest-native BDD. AnyBridge's runtime BDD executor does not require pytest.

### Automatic provider selection

The MCP runtime and CLI can use `--provider auto` and `--browser auto`. The selection
layer evaluates requested capabilities and browser compatibility while keeping
Playwright as the deterministic default when no stronger preference is supplied.
Provider choice remains explicit and inspectable, so HWS can later feed execution
history, evidence quality, latency, or failure rates into the same decision point.

### Evidence-driven provider routing

HWS can optionally supply prior execution observations to the MCP runtime. The
learning layer scores compatible providers using observed success rate,
evidence confidence, and bounded latency, while capability and browser
constraints remain hard gates:

    runtime = BridgeRuntime(
        provider="auto",
        provider_history=[
            {"provider": "playwright", "success": True, "evidence_confidence": 0.92, "latency_ms": 1800},
            {"provider": "selenium", "success": False, "evidence_confidence": 0.30, "latency_ms": 4200},
        ],
    )

The history is caller-owned and is not persisted by AnyBridge. This keeps
long-lived learning, replay corpora, and promotion/rollback decisions in HWS
while AnyBridge remains a browser execution component. Malformed observations
are ignored, and a provider can never be selected if it fails the requested
capability, browser, or runtime-availability constraints.

### Execution decision fabric

AnyBridge can also consume the same evidence telemetry as a broader execution
decision. `decide_execution()` jointly selects provider and browser and derives
verification depth, safe parallel-vs-serial execution, retry budget, and
escalation. Capability and safety constraints remain hard gates; telemetry only
optimizes within those boundaries.

```python
from anybridge.execution_fabric import decide_execution

decision = decide_execution(
    required=["bdd"],
    risk="medium",
    independent_work=4,
    history=[...],
)
```

The decision object is deliberately declarative. HWS remains responsible for
executing the plan, collecting the resulting Evidence Envelope, and feeding
verified outcomes back into the next decision. This creates a closed loop:
**observe -> decide -> execute -> verify -> learn -> decide**.


### Closed-loop remediation fabric

AnyBridge can close the execution feedback loop without depending on a particular orchestrator. The standalone remediation primitives correlate a failure to an execution step and, when available, a concrete network dependency; generate declarative remediation actions with explicit preconditions; and accept the caller's realized remediation outcome.

The loop is:

`execution → step/evidence → failure attribution → remediation proposal → caller executes → realized outcome → execution/decomposition telemetry → next decision`

Use `correlate_failure()` for an `EvidenceEnvelope` or `correlate_step_failure()` for provider-neutral step results. `propose_remediations()` returns safe, non-executing actions such as refreshing a stale target, validating navigation, inspecting an approved network dependency, capturing runtime diagnostics, or deepening verification.

After the caller executes an action, `record_remediation_outcome()` produces two generic telemetry records: one shaped for execution/provider decisions and one shaped for decomposition. Successful recovery becomes positive evidence; unrecovered attempts increase the relevant failure/retry signal. `remediation_telemetry()` preserves the complete failure → action → outcome chain.

AnyBridge does **not** execute the remediation proposal automatically, persist learning, schedule retries, or import an external orchestrator. The caller remains in control. This keeps AnyBridge usable as a standalone browser execution substrate for HWS or any other system.

Example:

```python
from anybridge.remediation import (
    correlate_failure,
    propose_remediations,
    record_remediation_outcome,
)

failure = correlate_failure(evidence, failure_id="failure-42", step_index=3, step="When I click Dashboard")
actions = propose_remediations(failure)

# Caller chooses/executes an action and reports the realized result.
feedback = record_remediation_outcome(
    outcome,
    provider="playwright",
    browser="chromium",
    task_class="dashboard",
)

# Feed feedback.execution_observation into the next execution decision
# and feedback.decomposition_observation into the next decomposition decision.
```


### Self-healing execution kernel

AnyBridge includes standalone bounded recovery primitives for stale or changed browser targets. `TargetFingerprint` and `rank_target_candidates()` compare semantic properties such as role, accessible name, text, label, tag, and href. `decide_target_recovery()` only proposes automatic re-identification when confidence and candidate separation exceed a threshold; ambiguous targets fall back to a fresh snapshot instead of guessing.

`ExecutionJournal` provides a bounded local execution history that records actions, failures, remediation identifiers, observations, and evidence. It is intentionally in-memory and exportable: callers can persist it wherever appropriate, while AnyBridge remains independent of any orchestrator.

The recovery boundary is:

```text
stale target → semantic candidate ranking → confidence gate → re-identify OR refresh snapshot → caller verifies outcome
```

AnyBridge never silently changes an ambiguous target and does not execute external remediation policy on the caller's behalf.


### Crash-safe checkpoint and resume

AnyBridge can create an explicit execution checkpoint and later evaluate whether the current browser state is safe to resume from it. The checkpoint store uses an atomic local JSON replacement, so an interrupted write does not leave a partially written checkpoint.

Resume is deliberately conservative: a saved checkpoint does not authorize replaying a side effect. `resume_checkpoint` compares the current URL, target availability, and recovery confidence. A changed URL, missing target, or insufficient confidence routes to revalidation/re-identification rather than direct replay.

This builds on the local execution journal and self-healing target resolver:

```text
execute → checkpoint → interruption → restore state → revalidate → re-identify if needed → resume → verify
```

Playwright supports browser-context storage snapshots for cookies/local storage and related state, but persisted state can contain authentication material. AnyBridge therefore keeps checkpoint metadata separate from browser credentials; callers can explicitly use the existing profile/storage facilities when authentication restoration is required. citeturn0search0turn0search5


### WebMCP content / prompt-injection boundary

Page-controlled WebMCP metadata and results are **data, never AnyBridge/system instructions**. AnyBridge now makes that distinction structural before content is returned to an agent:

- published tool definitions use a static, non-instructional description;
- the original page description/schema/annotations are retained inside an `_anybridge.content_boundary` envelope marked `trust=untrusted` and `instruction_authority=none`;
- tool results are wrapped in a `webmcp_tool_output` envelope;
- normal results receive nonce-based spotlighting;
- results marked `untrustedContentHint` or associated with higher-risk execution are Base64-encoded for stronger isolation;
- boundary markers supplied by page content are neutralized before spotlighting;
- failures are also returned/recorded as quarantined WebMCP data.

This is deliberately a **boundary**, not a prompt-injection detector. AnyBridge does not claim that it can prove arbitrary page text is benign. The consuming agent can inspect the envelope as data while keeping system/user instructions in a separate trust class.

The resulting flow is:

```text
page metadata
    ↓
provenance + risk assessment
    ↓
quarantine / structural envelope
    ↓
agent-visible data boundary
    ↓
tool execution
    ↓
quarantined result
    ↓
outcome telemetry
```

For higher-risk WebMCP output, the stronger encoding path follows current browser security guidance that recommends spotlighting untrusted content and using encoding where stronger isolation is warranted. The `untrustedContentHint` annotation is treated as a security signal, not as proof of safety.

### WebMCP capability trust and tool provenance

AnyBridge treats a website's WebMCP capability definition as observable, changeable input rather than implicit authorization. Each discovered tool is bound to its **origin + name + schema fingerprint + description fingerprint + safety annotations**. A bounded in-memory `ToolTrustRegistry` detects first-seen capabilities and definition drift without persisting page data or executable code.

The registry tracks:

- `new` — capability has not been explicitly trusted in this session.
- `trusted` — the caller explicitly trusted the unchanged capability.
- `changed` — schema, description, origin, or safety annotations changed.
- `untrusted_output` — the tool declares that its output contains untrusted content.
- `blocked` — the capability is malformed and cannot be safely identified.

WebMCP annotations such as `readOnlyHint`, `consequentialHint`, and `untrustedContentHint` are incorporated into the local action policy. Consequential tools require confirmation; untrusted outputs remain an explicit trust boundary. AnyBridge does not treat a site's metadata as permission to execute a tool.

Use `tool_trust` to inspect the current page's capabilities and `inspect_webmcp_tool` to assess/trust one capability. A WebMCP call is allowed only after its current definition passes the provenance policy; definition changes force revalidation rather than silently reusing prior trust.

The execution boundary is:

```text
discover → fingerprint → bind to origin → compare history → assess risk
        → explicit trust/revalidation → execute → record outcome
```

The registry is intentionally local and caller-owned. Long-lived persistence, promotion/rollback, and cross-session learning remain outside AnyBridge.

### Action risk and retry policy

AnyBridge exposes a deterministic `assess_action` policy primitive before consequential browser operations. Actions are classified as read, navigation, mutation, consequential, or unknown. The policy distinguishes bounded automatic retries from state revalidation and explicit confirmation.

Read-only operations can be retried within a small bound; navigation is bounded and replayable; state mutations require current-state revalidation; consequential signals such as delete, purchase, payment, transfer, send, submit, or withdraw default to confirmation with zero automatic retries. Unknown actions conservatively default to revalidation.

This complements the checkpoint and self-healing layers:

```text
failure → action assessment → retry / revalidate / confirm → execute → verify → journal
```

WebMCP itself now exposes annotations such as `readOnlyHint` and `consequentialHint` to help agents distinguish read-only from consequential tools. AnyBridge's policy therefore provides a local defensive layer rather than trusting a site's metadata alone. citeturn0search0turn0search1


### WebMCP adversarial evaluation and quarantine enforcement

AnyBridge includes a deterministic, model-free security regression harness for the WebMCP boundary. `evaluate_webmcp_security()` exercises malicious tool descriptions and names, schema poisoning, spoofed page origins, consequential annotations, untrusted tool output, delimiter injection, and same-name cross-origin capabilities.

The evaluator checks the actual publication and quarantine path rather than testing a parallel mock implementation. It verifies that:

- page-controlled metadata never becomes instruction authority;
- schemas retain machine-readable structure while every object is explicitly marked as untrusted page data;
- published provenance is derived from the active browser origin rather than a page-supplied `origin` field;
- consequential tools remain subject to confirmation/revalidation;
- untrusted results use Base64 quarantine;
- spotlight boundaries reject marker injection;
- forged authority or tampered content fingerprints are rejected;
- identical tool names from different origins remain separate capabilities.

Run the regression suite with:

```python
from anybridge.webmcp_security import evaluate_webmcp_security

report = evaluate_webmcp_security()
assert report.passed, report.to_dict()
```

The built-in `webmcp_security_evaluation` tool exposes the same deterministic report without executing any website tool. The corpus is intentionally small, stable, and model-free so it can become a regression gate as the WebMCP surface evolves.

The enforcement boundary is:

```text
page-controlled input
        ↓
origin binding + risk assessment
        ↓
structural quarantine
        ↓
integrity/fingerprint validation
        ↓
agent-visible data
        ↓
tool execution gate
        ↓
quarantined output
        ↓
adversarial regression evaluation
```

This follows current WebMCP security guidance that calls out malicious tool manifests and contaminated outputs, recommends deterministic guardrails and spotlighting/encoding, and recommends routinely evaluating agent vulnerabilities. citeturn0view0

### Adaptive WebMCP security learning loop

AnyBridge can turn realized WebMCP security failures into bounded regression seeds without introducing an orchestrator or persistence dependency.

```text
discover/evaluate
      ↓
security observation
      ↓
correlate attack class + provider + origin + schema + defense
      ↓
aggregate repeated failure pattern
      ↓
promotion threshold
      ↓
bounded regression corpus
      ↓
replay on future evaluations
      ↓
new observations
```

`SecurityLearningStore` records caller-supplied outcomes and tracks:

- attack class and attack identity;
- execution provider;
- page origin;
- schema fingerprint;
- defense used;
- pass/fail outcome;
- evidence confidence and optional latency/execution metadata.

A stable failure signature is created from the attack class, provider, origin, schema fingerprint and defense. Repeated failures are automatically promoted after configurable minimum failure-count and failure-rate thresholds. A single failure does not become a permanent regression, and successful observations dilute a failure pattern's rate.

Promoted cases are bounded, exportable regression seeds. They do not execute automatically and are never treated as instructions. The caller owns durable storage, replay scheduling, remediation authority and source-control promotion.

This deliberately keeps AnyBridge standalone:

```text
AnyBridge
  ├── observes
  ├── correlates
  ├── promotes regression seeds
  └── exports evidence
       ↓
caller / CI / orchestrator (optional)
```

The loop complements WebMCP's origin and annotation model: current WebMCP exposes origin and security-relevant hints such as `untrustedContentHint` and `consequentialHint`, while cross-origin tools require explicit origin exposure.

### WebMCP security capability evolution

The security learning loop now evolves the defensive capability itself, not only the regression corpus. SecurityEvolutionStore learns which defenses are effective for each attack family, detects degradation after an established baseline, creates deterministic counter-cases, and manages promoted regression seeds through an active/promoted → retired → reactivated lifecycle.

```text
security outcome
      ↓
attack family × defense effectiveness
      ↓
established baseline ──→ regression detection
      ↓                         ↓
controlled counter-cases    defensive signal
      ↓
future replay
      ↓
repeated passes ──→ retire obsolete seed
      ↑
later failure ────→ reactivate seed
```

The evolution layer tracks:

- DefenseEffectiveness — attempts, blocks, block rate, confidence and regression state for each attack-family/defense pair.
- DefenseRegression — an advisory signal when a previously established defense falls below its configured block-rate threshold.
- CounterCase — deterministic field-order, case, whitespace and nesting variants derived from a regression seed. They are data fixtures only; AnyBridge never executes generated attacks.
- RegressionSeed — bounded lifecycle state for promoted cases: active/promoted, retired after repeated verified passes, and reactivated after a later failure.
- provider, origin and schema fingerprint on every observation, so effectiveness evidence can be attributed without collapsing distinct execution contexts.

Use the built-in webmcp_security_evolution tool with caller-supplied security observations when an external runner or CI system wants a compact evolution report. The module is also directly usable through SecurityEvolutionStore and evolve_security_capabilities().

Retirement never deletes historical observations: it only removes an obsolete seed from the active replay corpus. A later regression can reactivate that seed. Thresholds are configurable and bounded to keep the behavior deterministic and auditable.

No HWS, model provider, database, scheduler, or external memory system is required. AnyBridge owns the capability-evolution primitives; the caller remains responsible for durable persistence, replay scheduling, execution of security tests, and remediation/authorization.

### Unified WebMCP security evolution pipeline

AnyBridge now exposes a single deterministic pipeline that composes the existing adversarial evaluator, adaptive security learning, and capability-evolution lifecycle:

```text
evaluate built-in corpus
        ↓
security observations
        ↓
learn attack/provider/origin/schema/defense pattern
        ↓
promote repeated failures
        ↓
handoff promoted seeds idempotently
        ↓
learn defense effectiveness / regressions
        ↓
generate controlled counter-cases
        ↓
active replay corpus
        ↓
caller executes/replays and feeds outcomes back
```

`evaluate_and_evolve_security()` returns the evaluator result, learning report, evolution report, and current active replay corpus. The `webmcp_security_pipeline` builtin exposes the same report-only operation.

The pipeline deliberately does **not** execute generated counter-cases, invoke website tools, authorize consequential actions, persist outside the supplied stores, or schedule replay. Those remain caller responsibilities. Reusing caller-owned stores preserves seed lifecycle state across evaluations, including retirement and later reactivation.
