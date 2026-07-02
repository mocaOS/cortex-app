# Chapter 18: Agent Skills

Agent Skills extend the Cortex Library's Deep Research and Chat capabilities with external instructions and live API access from the open [AgentSkills](https://agentskills.io/) ecosystem. Skills are reusable capability packages — each is a `SKILL.md` file that teaches the researcher agent how to interact with external services. The agent uses a built-in `http_request` tool to call APIs described in skill instructions, with authentication handled entirely server-side.

## How Skills Work

The Library uses an **auto-activation** pattern. All enabled skills are automatically loaded at the start of every research or chat session. The full SKILL.md body is injected into the system prompt so the agent sees skill instructions from the very first iteration.

```
User installs "web-search" skill from the registry
  → Setup wizard prompts for API key
  → User provides key, saved to config.json
  → Skill enabled via toggle

User asks: "Search the web for recent news about Ethereum"

Agent sees skill instructions in system prompt (auto-activated):
  <active_skills>
    <skill name="web-search">
      Use GET https://api.example.com/search?q={query} to search...
    </skill>
  </active_skills>

Agent calls: http_request(method="GET", url="https://api.example.com/search?q=Ethereum+news")
  → Server injects auth headers from config schema
  → HTTP call made with authentication
  → Response truncated intelligently, returned to agent
```

### Activation Flow

1. **Auto-activation** — At session start, all enabled skills are loaded from the filesystem. Their SKILL.md bodies are injected into the system prompt inside `<active_skills>` tags, with auth-related lines stripped out.
2. **`http_request` tool** — A built-in tool (not per-skill) is added to the agent's tool list when any skill is active. The agent provides only `method` and `url` — no headers parameter exists. Authentication is injected server-side.
3. **Server-side auth** — When `http_request` is called, the server builds auth headers from each skill's config schema (`auth_header` field) and config values (`config.json`). The LLM never sees or handles tokens.
4. **Smart truncation** — API responses are intelligently truncated to fit within context. For JSON arrays, individual items are slimmed (long string values shortened) while preserving all entries.

### Speed Mode (Chat) with Skills

When skills are active, speed mode (chat) gains the `reasoning` tool (normally quality-only) so the agent can process large API responses before deciding its next step. Max iterations are bumped to 3 (configurable via `RESEARCHER_MAX_ITERATIONS_SPEED`). Agent-based chat is enabled by default (`ENABLE_AGENT_CHAT=true`).

## Installing Skills

### From the Settings Page

Navigate to **Settings > Agent Skills**. Three installation methods are available:

1. **URL** — Paste a direct link to a SKILL.md file (e.g., a GitHub raw URL)
2. **Registry** — Search the [skills.sh](https://skills.sh) registry and install with one click
3. **Local discovery** — Place skill directories in the configured `SKILLS_DIR` and click "Discover"

After installation, skills that require configuration (API keys, tokens, etc.) will show a **"Needs setup"** badge. Click **"Configure"** in the expanded skill details to open the setup wizard.

### From the skills.sh Registry

The Skills section includes a search bar that queries the official [skills.sh](https://skills.sh) registry (600+ skills). Search results show skill name, author/repo, and install count. Click "Install" to download the SKILL.md and register it.

### Manual Installation

Place a skill directory in the skills directory (default: `.agents/skills/`):

```
.agents/skills/
  web-search/
    SKILL.md          # Required: skill definition
    config.json       # Auto-created: configuration values (via setup wizard)
    references/       # Optional: documentation files
```

Then click **Discover** on the Settings page or restart the application.

## SKILL.md Format

Skills follow the [AgentSkills open standard](https://agentskills.io/specification). A SKILL.md file has YAML frontmatter and a markdown body:

```markdown
---
name: web-search
description: Search the web for current information using a search API.
license: Apache-2.0
metadata:
  author: example-org
  version: "1.0"
---

# Web Search

Use this skill when the user needs current information that isn't in the knowledge base.

## When to Use
- Questions about recent events
- Requests for up-to-date data
- Anything where the knowledge base might be outdated

## API Endpoints

Search the web:
GET https://api.example.com/search?q={query}&count=10

Returns JSON with a `results` array of `{title, url, snippet}` objects.
```

The SKILL.md body is what gets injected into the agent's system prompt. It should describe when to use the skill and which API endpoints to call. The agent will use `http_request` to call those endpoints directly — authentication is handled automatically.

### Required Fields

| Field | Description |
|-------|-------------|
| `name` | Lowercase, hyphens allowed. 1-64 characters. |
| `description` | What the skill does and when to use it. 1-1024 characters. |

### Optional Fields

| Field | Description |
|-------|-------------|
| `license` | License name (e.g., `Apache-2.0`) |
| `metadata` | Arbitrary key-value pairs. Common: `author`, `version` |
| `compatibility` | Environment requirements |

## The `http_request` Tool

When any skill is active, the researcher agent gets a built-in `http_request` tool. This is a single, shared tool — not per-skill.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `method` | string | Yes | HTTP method: `GET`, `POST`, `PUT`, `PATCH`, or `DELETE` |
| `url` | string | Yes | The full API URL to call |
| `body` | string | No | Optional request body (for POST/PUT/PATCH) |

There is no `headers` parameter. Authentication headers are built and injected server-side from the skill's config schema.

### How Auth Injection Works

1. When the agent calls `http_request`, the server figures out which activated skills are allowed to authenticate against the request URL — only skills whose known hostname matches the URL's host. A skill's hostname comes from either the `base_url` auto-extracted from its SKILL.md or from any URL-shaped value in its `config.json` (e.g. `*_BASE_URL`).
2. For each matching skill, the server iterates the schema's `auth_header` fields (e.g., `"Authorization: Bearer API_TOKEN"`), looks up the corresponding value from `config.json`, and substitutes the placeholder.
3. The resulting headers are added to the outgoing HTTP request.

This hostname scoping is what prevents two installed skills that both define `Authorization` headers (e.g. one using `Bearer`, another using `Token token=`) from silently overwriting each other.

The LLM never sees API keys, tokens, or auth headers. Lines in the SKILL.md that mention token replacement or ask the user to provide credentials are stripped before injection into the system prompt.

### Response Handling

- Responses are truncated to 32,000 characters maximum
- JSON responses with arrays are truncated intelligently: individual items are slimmed (long string values shortened) while preserving all array entries, so the agent sees complete data sets rather than cut-off JSON
- Non-JSON responses are truncated with a hard character limit
- Every truncated response carries an explicit `[NOTE: response truncated …]` trailer with pagination guidance (`?limit=`, `?page=`), so the agent knows data was dropped and can fetch the rest instead of answering from partial data
- The answer stage additionally caps oversized API payloads (~8,000 characters per skill source) — the agent analyzed the full data during research; the writer doesn't re-pay for it
- Successful API responses are also stored as sources for the writer phase, so they appear in the final answer's references

### Failed Calls

When an API call returns an error status (e.g. `401`, `403`, `422`, `500`) or times out, the failure is surfaced rather than hidden:

- **In the chat** — a red skill step appears in the research process showing the failing method, URL, and HTTP status code (for example, `API call failed: POST https://.../tickets → HTTP 403`).
- **In the answer** — the assistant explicitly states that the attempted action did not succeed and explains why, using the API's error message. It will not claim or imply that a failed action (such as creating a ticket) completed.

If a write action keeps failing, check: the skill's token is configured and current (Setup Wizard), the token's account has permission for that action, and the request matches what the target API expects. See also [Troubleshooting](#troubleshooting).

## Setup Wizard and Config System

Skills that interact with external APIs typically need configuration — API keys, tokens, base URLs. The setup wizard handles this automatically.

### How It Works

1. **LLM analysis** — After installation, the primary LLM analyzes the SKILL.md body to extract required configuration variables (API tokens, URLs, etc.) and the skill's API base URL (when hardcoded in the docs). The LLM returns both the variable schema and the `base_url`.
2. **Schema caching** — The extracted schema is stored on the Neo4j `Skill` node as `config_schema` (JSON string) alongside `base_url`. This avoids re-analyzing on every page load. `base_url` is used to scope auth headers by hostname — never shown to the user; it's an internal anchor.
3. **Setup wizard modal** — When the user opens configuration (or when schema has not yet been analyzed), a modal presents each variable with a labeled input field. Secret-type variables get masked input with show/hide toggle.
4. **Config persistence** — Values are saved to `config.json` in the skill's directory. Secret values are masked (`********`) in API responses; submitting the mask preserves the existing value.

### Config Schema Format

Each variable in the schema has these fields:

| Field | Description |
|-------|-------------|
| `name` | Variable name in `SCREAMING_SNAKE_CASE` (e.g., `API_TOKEN`) |
| `description` | One-sentence explanation of what this variable is and where to find it |
| `required` | Boolean — whether the skill cannot function without it |
| `type` | `"secret"` for tokens/passwords/API keys, `"text"` for URLs/identifiers |
| `auth_header` | (Optional) The HTTP header template for auth variables. Example: `"Authorization: Bearer API_TOKEN"`. The variable name acts as the placeholder. |

### Config Status

Skills display their configuration status in the UI:

| Status | Badge | Meaning |
|--------|-------|---------|
| `needs_setup` | "Needs setup" | Required config variables exist but are not yet provided |
| `configured` | None | All required variables have values |
| (no schema) | None | Skill requires no configuration |

## Enabling and Disabling Skills

Installed skills are **disabled by default**. Toggle them on/off from the Settings page. Only enabled skills are auto-activated in research/chat sessions.

When a skill is disabled:
- It is not loaded at session start
- Its instructions are not injected into the system prompt
- No prompt tokens are consumed

When a skill is enabled but needs setup:
- It will be loaded, but API calls will fail due to missing authentication
- The "Needs setup" badge prompts the user to configure it first

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_SKILLS` | `true` | Master switch for the skills system |
| `SKILLS_DIR` | `.agents/skills` | Directory for skill discovery (relative to project root or absolute) |
| `ENABLE_SKILL_SCRIPTS` | `false` | Allow legacy `tools.json` skills to execute local scripts. **Security-sensitive** — only enable if you trust all installed skills. |
| `SKILL_SCRIPT_TIMEOUT` | `30` | Timeout in seconds for legacy script execution |
| `SKILL_HTTP_TIMEOUT` | `15` | Timeout in seconds for HTTP tool calls |
| `MAX_SKILL_TOOLS` | `10` | Maximum total legacy skill-provided tools in the researcher agent |
| `MAX_SKILL_INSTRUCTIONS_TOKENS` | `4000` | Approximate token budget for skill instructions in the system prompt. Enforced: oversized instruction blocks are cut with an explicit truncation marker, so many enabled skills can't grow the prompt without bound |
| `ENABLE_AGENT_CHAT` | `true` | Enable agent-based chat mode (required for skills in chat) |
| `RESEARCHER_MAX_ITERATIONS_SPEED` | `3` | Max agent loop iterations in speed/chat mode |
| `RESEARCHER_MAX_ITERATIONS_QUALITY` | `8` | Max agent loop iterations in quality/deep research mode |

### Docker Persistence

The skills directory must be persisted across container restarts:

- **Production / Coolify**: Named volume `skills_data:/app/.agents/skills`
- **Development**: Bind mount via `docker-compose.yml`

Both `docker-compose.prod.yml` and `coolify/docker-compose.coolify.yml` include the `skills_data` named volume by default.

## API Endpoints

All skill endpoints require **Admin** authentication.

### Skill Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/admin/skills` | List all installed skills (includes `config_status`) |
| `GET` | `/api/admin/skills/{skill_id}` | Skill details (includes SKILL.md body) |
| `POST` | `/api/admin/skills/install` | Install from URL or registry. Body: `{url?}` or `{registry_id?}` |
| `PATCH` | `/api/admin/skills/{skill_id}` | Enable/disable. Body: `{enabled: true}` |
| `DELETE` | `/api/admin/skills/{skill_id}` | Uninstall skill and delete files |
| `GET` | `/api/admin/skills/registry/search` | Search skills.sh. Query: `q` |
| `POST` | `/api/admin/skills/discover` | Re-scan local skills directory |

### Skill Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/admin/skills/{skill_id}/analyze` | Run LLM analysis of SKILL.md to extract config variables. Returns `{skill_id, variables}`. Caches schema in Neo4j. |
| `GET` | `/api/admin/skills/{skill_id}/config` | Get config schema + current values (secrets masked as `********`). Returns `{skill_id, schema, values}`. |
| `PUT` | `/api/admin/skills/{skill_id}/config` | Save config values. Body: `{values: {KEY: "value"}}`. Submitting `********` preserves existing secret. |

## Security Considerations

- **Server-side auth injection.** The LLM never sees API keys or tokens. Auth headers are built from config schema templates and config values on the server. Auth-related lines are stripped from SKILL.md before injection into the system prompt.
- **Config value masking.** The `GET /config` endpoint masks secret-type values as `********`. The `PUT /config` endpoint preserves existing secrets when the mask placeholder is submitted.
- **Admin-only management.** All skill installation and configuration endpoints require admin authentication.
- **Script execution is disabled by default.** The `ENABLE_SKILL_SCRIPTS` flag must be explicitly set to `true` for legacy `tools.json` skills that use script execution. Only enable this if you trust all installed skills.
- **Prompt injection defense.** Anti-injection instructions are appended after skill content in the system prompt.
- **Response truncation.** API responses are capped at 32,000 characters with intelligent JSON slimming to prevent context overflow.
- **HTTP timeout.** External API calls have a 15-second timeout to prevent hanging.

## Troubleshooting

**Skills not appearing in research/chat?**
- Ensure the skill is **enabled** (toggled on in Settings)
- Ensure `ENABLE_SKILLS=true` in your environment
- Check that the SKILL.md has a valid `description` field — skills without descriptions are skipped
- For chat mode, ensure `ENABLE_AGENT_CHAT=true`

**Skill shows "Needs setup" badge?**
- Click **Configure** in the expanded skill details to open the setup wizard
- The LLM will analyze the SKILL.md and prompt you for required values (API keys, tokens, etc.)
- After saving, the badge should disappear

**API calls returning auth errors?**
- A failed call is now visible directly in the chat: look for a **red skill step** in the research process showing the method, URL, and status code, and the answer will state that the action did not succeed
- A `401` usually means no/invalid token; a `403` means the token is valid but the account lacks permission for that action; a `422` means the request payload was rejected as invalid
- Open the skill's configuration and verify that the API key/token is correct
- Check that the config schema has the right `auth_header` template (e.g., `"Authorization: Bearer API_TOKEN"`)
- Look at backend logs for `http_request: METHOD URL | auth=yes/none` to verify auth injection

**API responses seem incomplete?**
- Large responses are intelligently truncated to 32,000 characters. For JSON arrays, all items are preserved but individual string fields may be shortened
- If this is insufficient, consider using more targeted API endpoints that return smaller payloads

**Registry search returns no results?**
- The search uses the skills.sh API (`https://skills.sh/api/search`). If the API is unreachable, check network connectivity from the backend container.

**Installed skill disappeared after restart?**
- Skills are stored on the filesystem in `SKILLS_DIR`. In Docker, this directory must be mounted as a volume to persist across container restarts. Check that `skills_data` volume is configured in your docker-compose file.

**Setup wizard shows no variables but API calls fail?**
- The LLM analysis may have missed required variables. Delete the skill and reinstall, or manually create a `config.json` in the skill's directory with the needed values.
