# Agent Skills

Extensible skill system for Deep Research and Chat using **auto-activation**, following the [AgentSkills open standard](https://agentskills.io/). See [`.claude/environment.md`](../environment.md#skills-configuration) for env vars.

## Skill Types

- **Instruction skills** — SKILL.md body modifies researcher behavior
- **Tool-providing skills** — legacy `tools.json` with HTTP/script execution config (not part of the agentskills.io standard; the standard pattern uses the built-in `http_request` tool with API endpoints described in SKILL.md)

## SKILL.md Format

Skills are SKILL.md files with YAML frontmatter (name, description, license, metadata).

## Discovery & Installation

- Skills discovered from `.agents/skills/` directory on startup
- Installable from direct URLs or skills.sh registry (`GET https://skills.sh/api/search?q={query}&limit=20`)
- Neo4j `Skill` nodes track metadata, enabled state, and `config_schema` (JSON)

## Auto-Activation Pattern

All enabled skills are loaded at researcher agent session start:
- Their SKILL.md instructions are injected into `<active_skills>` block
- The built-in `http_request` tool is added to the tool list
- `build_activated_skills_block()` strips auth-related lines from SKILL.md to prevent the model from handling auth itself
- The agent can also manually activate additional skills mid-conversation via `activate_skill`/`list_skills` tools (kept for backward compatibility)

### Key Functions
- `get_skill_catalog()` — tier 1, Neo4j only
- `load_skill_for_activation()` — tier 2, reads filesystem + config.json
- `get_tools_with_skill_activation()` — dynamic tool list (adds `http_request` + `reasoning` to speed mode when skills active)

## HTTP Request Tool

Built-in `http_request` tool defined in `research_prompts.py`, executed in `researcher_agent.py` — **no `headers` parameter** (auth is server-side).

### Server-Side Auth Injection (hostname-scoped)
`http_request` builds HTTP headers from `auth_header` fields in each activated skill's config schema (e.g., `"Authorization: Bearer API_TOKEN"` → header with actual value from config.json), then calls the API via httpx. Headers are **scoped by hostname**: only skills whose known hostname matches the request URL contribute headers. Without this, two skills that both set `Authorization` would silently overwrite each other.

A skill's known hostnames are derived from (a) the LLM-extracted `base_url` stored on the Neo4j Skill node, and (b) any URL-shaped value in its `config.json` (e.g. `*_BASE_URL` config variables). When no skill matches the request host, falls back to skills with no URL hint and logs a warning if more than one such skill could collide on header names.

### Variable Substitution
`_substitute_variables()` handles `${VAR}` and bare `VAR` patterns in URLs/bodies, resolving from skill config values first then `SKILL_*` env vars. Pass 1 replaces `${VAR}`; pass 2 replaces bare uppercase `KEY` tokens **only for keys not already substituted in pass 1** (tracked via a `replaced` set). It must not skip a bare key just because its value coincidentally appears elsewhere in the text — otherwise placeholders like `ZAMMAD_GROUP_NAME` leak through literally when the value (e.g. `Users`) shows up in the body.

### TLS Verification
The `http_request` httpx client verifies TLS by default. Hosts listed in `SKILL_HTTP_INSECURE_HOSTS` (comma-separated) skip verification — opt-in, per-host, for self-hosted skill APIs with self-signed certs. See [`.claude/environment.md`](../environment.md#skills-configuration).

### Forcing a tool call (reliability)
On iteration 0 with skills active, if the model replies with prose instead of calling a tool, the agent first tries `tool_choice="required"`, then **falls back to a corrective system nudge + `tool_choice="auto"`** if `required` errors (some providers, e.g. Venice/minimax, return HTTP 500 on forced tool choice) or yields no tool call. Without the fallback, skill actions (e.g. a ticket POST) silently never fire on prose-first turns.

### Failure Surfacing
A non-2xx response (or timeout) is caught and turned into an `"Error: ..."` tool-result string for the model **and** surfaced three further ways so a failed call (e.g. a Zammad ticket POST returning 403/422) can't masquerade as success:
1. A `skill_tool` stream event with `is_error: True` is emitted (`researcher_agent.py`, in the `http_request` except handlers), which the frontend renders as a red Puzzle-icon step (`[SkillError]` prefix in `thinkingSteps`; see `AskPanel.tsx`/`ChatMessage.tsx`).
2. The failure is appended to `result.sources` as a `skill_api_error`-typed entry (score 0, content prefixed `NOTE: This skill API call FAILED...`).
3. The failure is recorded on `ResearchResult.failed_actions` and injected into the **writer** user prompt as a `=== Failed Actions (MUST report to the user) ===` section via `get_writer_user_prompt(..., failed_actions=...)` (`research_prompts.py`). This is the reliable channel — a score-0 source alone can be crowded out or trigger the writer's no-context/anti-injection deflection, whereas the explicit instruction makes the writer state the action did not succeed and why. See [`rag-pipeline.md`](rag-pipeline.md#writer).

### Response Truncation
`_truncate_response()` intelligently truncates large JSON API responses by progressively slimming array items (truncating string values, flattening nested objects, compacting lists) before falling back to item-level truncation.

### Source Handling
Successful API responses are stored as sources (without chunk_id) for the writer. `_deduplicate_sources()` keeps sources without chunk_id (skill API responses) alongside chunk-based deduplication.

## Config Wizard (Setup)

### LLM Analysis
`POST /api/admin/skills/{id}/analyze` sends SKILL.md body to the primary LLM which returns `{variables: [...], base_url: string|null}`. Each variable: name, description, required, type, optional auth_header. Schema cached on the Neo4j `Skill` node as `config_schema`; `base_url` cached on the same node (auto-extracted, never user-edited — used to scope auth headers when the skill has no URL config variable).

**Prompt is tuned for small primary models** (e.g. gemma-4-26b). The old prompt returned `{}` for SDK-style skills whose only credential appears as a placeholder (`apiKey: "YOUR_API_KEY"`) with no explicit env var or URL — the AgentMail skill is the canonical failure. The current prompt: keeps it short, gives one worked SDK example, explicitly treats placeholder/SDK-arg credentials **and** `{TEMPLATE}`/`UPPER_SNAKE` vars (self-hosted REST) as required, infers `base_url` from the product name when unprinted, and omits any "return empty if nothing needed" escape hatch (small models latch onto it and bail).

**Robustness (these models are non-deterministic even at temperature 0):**
- `_extract_json_object()` — tolerant parse: strips fences, falls back to the first `{...}`/`[...]` span, returns `None` (not an exception) on truncated/garbage output, so a stray token can no longer silently save an empty schema.
- `analyze_skill_config()` retries up to `_SKILL_ANALYSIS_MAX_ATTEMPTS` (3) on an empty result, accepting the first attempt that yields any variable or base_url. **Retry is gated on `_skill_doc_hints_config(body)`** — it fires only when the docs mention a credential keyword, an `{UPPER_TEMPLATE}` placeholder, or an `ENV_STYLE_NAME` suffix. A keyless API (e.g. wttr.in weather) or pure instruction skill is *correctly* empty and accepts in a single call. This gating was the key latency fix: an ungated retry made keyless skills pay 3× the per-call latency (minutes on a slow gateway).
- `max_tokens` uses the global `OPENAI_MAX_OUTPUT_TOKENS` (`settings.openai_max_output_tokens`, default 8000), not a hardcoded value. The primary/extraction models are **reasoning models**: measured completion is ~900–1100 tokens even for a 0–1 variable skill (almost all thinking), and a ~10-variable schema reaches ~2800. A tight cap (e.g. the old 1500) truncates the thinking or the JSON and yields a spurious empty/partial result, so the budget must stay large.

**Latency note:** per-call latency is dominated by the gateway, not the prompt. Venice-hosted gemma/qwen run ~10–15s; the same gemma routed through Cloudflare AI Gateway ran ~35–58s. The model is read from `OPENAI_MODEL`/`OPENAI_API_BASE` (`env_file: .env`), which is only re-read on container **recreate**, not `docker restart` — a stale route is a common cause of slow analysis.

### Config Endpoints
- `GET /api/admin/skills/{id}/config` — schema + masked values
- `PUT /api/admin/skills/{id}/config` — save with mask preservation for existing secrets

### Secret Encryption
Secret-typed fields (schema `type: "secret"`) are encrypted at rest in `config.json` (`enc:`-prefixed Fernet ciphertext via `crypto_service.py`) when `ENCRYPTION_KEY` is set; plaintext otherwise (startup migration encrypts existing values once a key is configured). `save_skill_config()` encrypts; `load_skill_for_activation()` is the only decrypt point (feeds `_substitute_variables` at runtime) and raises a clear "reconfigure this skill" error if a value is undecryptable. `get_skill_config()` returns stored (possibly ciphertext) values — fine for its callers (masking, PUT-merge, export). Library exports strip secret fields from `config.json` entirely.

### Config Status
`config_status` field on `SkillInfo` response: "configured" (all required vars have values), "needs_setup" (schema exists but values missing), or null (no schema yet).

### Models
Config models in `models.py`: `SkillConfigVariable`, `SkillConfigSchema`, `SkillConfigSaveRequest`.

## Legacy Tool Execution

Skill tool names namespaced as `skill__{skill_id}__{tool_name}`. Legacy tool execution via `SkillService.execute_skill_tool()`: HTTP tools use httpx with env var substitution (only `SKILL_*` vars for security), script tools gated by `ENABLE_SKILL_SCRIPTS` (disabled by default).

## Frontend

- `SkillsManager` component on Settings page (install/enable/disable/delete + registry search via skills.sh API + setup wizard trigger)
- `SkillConfigModal` for config setup: fetches/triggers LLM analysis of SKILL.md to extract config schema, displays form fields (text/secret types with visibility toggle), saves values via PUT config endpoint, mask preservation for existing secrets
- Skill activations and tool calls rendered in chat with Puzzle icon
- Skills directory persistence: `skills_data` Docker volume mounted at `/app/.agents/skills` in `docker-compose.prod.yml`

## API Endpoints

All admin-only:
- `GET/POST/PATCH/DELETE /api/admin/skills/*`
- `POST /api/admin/skills/{id}/analyze`
- `GET /api/admin/skills/{id}/config`
- `PUT /api/admin/skills/{id}/config`
