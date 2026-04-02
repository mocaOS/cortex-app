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

### Server-Side Auth Injection
`_execute_http_request` merges all activated skill configs, builds HTTP headers from `auth_header` fields in each skill's config schema (e.g., `"Authorization: Bearer API_TOKEN"` → header with actual value from config.json), and calls the API via httpx.

### Variable Substitution
`_substitute_variables()` handles `${VAR}` and bare `VAR` patterns in URLs, resolving from skill config values first then `SKILL_*` env vars.

### Response Truncation
`_truncate_response()` intelligently truncates large JSON API responses by progressively slimming array items (truncating string values, flattening nested objects, compacting lists) before falling back to item-level truncation.

### Source Handling
Successful API responses are stored as sources (without chunk_id) for the writer. `_deduplicate_sources()` keeps sources without chunk_id (skill API responses) alongside chunk-based deduplication.

## Config Wizard (Setup)

### LLM Analysis
`POST /api/admin/skills/{id}/analyze` sends SKILL.md body to the primary LLM which returns a JSON array of `SkillConfigVariable` objects (name, description, required, type, auth_header). Schema cached on the Neo4j `Skill` node as `config_schema`.

### Config Endpoints
- `GET /api/admin/skills/{id}/config` — schema + masked values
- `PUT /api/admin/skills/{id}/config` — save with mask preservation for existing secrets

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
