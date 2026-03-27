# Chapter 19: Agent Skills

Agent Skills extend the Cortex Library's Deep Research and Chat capabilities with external tools and instructions from the open [AgentSkills](https://agentskills.io/) ecosystem. Skills are reusable capability packages — each is a `SKILL.md` file with optional tool definitions — that the researcher agent can activate on demand during a conversation.

## How Skills Work

The Library uses an **on-demand activation** pattern. Rather than loading all skills into every query, the researcher agent sees a compact catalog of available skills and decides which ones to activate based on the user's question.

```
User asks: "Search the web for recent news about Ethereum"

Agent sees catalog:
  - web-search: Search the web for current information. [type: tool]
  - find-skills: Discover and install agent skills. [type: instruction]

Agent decides: activate_skill("web-search")
  → Full instructions loaded into context
  → skill__web-search__search tool becomes callable

Agent calls: skill__web-search__search(query="Ethereum recent news")
  → HTTP call to the skill's configured endpoint
  → Results returned to agent for synthesis
```

### Activation Flow

1. **Catalog injection** — On every query, the system prompt includes an `<available_skills>` block listing each enabled skill's name and one-line description (~50 tokens per skill)
2. **Agent decides** — The LLM sees `activate_skill` and `list_skills` in its tool list and chooses whether any skill is relevant
3. **On-demand loading** — When `activate_skill(name)` is called, the full SKILL.md body is loaded into context and the skill's tools (if any) become callable
4. **Persistence** — Activated skills remain active for the rest of the conversation

### Skill Types

| Type | Description | Example |
|------|-------------|---------|
| **Instruction** | SKILL.md body modifies how the agent behaves using existing built-in tools | `find-skills`: teaches the agent how to help users discover skills |
| **Tool** | Includes a `tools.json` that defines callable HTTP endpoints or scripts | `web-search`: adds a `search` tool that calls an external search API |

## Installing Skills

### From the Settings Page

Navigate to **Settings > Agent Skills**. Three installation methods are available:

1. **URL** — Paste a direct link to a SKILL.md file (e.g., a GitHub raw URL)
2. **Registry** — Search the [skills.sh](https://skills.sh) registry and install with one click
3. **Local discovery** — Place skill directories in the configured `SKILLS_DIR` and click "Discover"

### From the skills.sh Registry

The Skills section includes a search bar that queries the official [skills.sh](https://skills.sh) registry (600+ skills). Search results show skill name, author/repo, and install count. Click "Install" to download the SKILL.md and register it.

### Manual Installation

Place a skill directory in the skills directory (default: `.agents/skills/`):

```
.agents/skills/
  web-search/
    SKILL.md          # Required: skill definition
    tools.json        # Optional: tool definitions for tool-providing skills
    scripts/          # Optional: executable scripts
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

## Instructions
1. Formulate a concise search query
2. Call the search tool with the query
3. Synthesize results with knowledge base context
```

### Required Fields

| Field | Description |
|-------|-------------|
| `name` | Lowercase, hyphens allowed. 1-64 characters. |
| `description` | What the skill does and when to use it. 1-1024 characters. Critical — the agent uses this to decide whether to activate. |

### Optional Fields

| Field | Description |
|-------|-------------|
| `license` | License name (e.g., `Apache-2.0`) |
| `metadata` | Arbitrary key-value pairs. Common: `author`, `version` |
| `compatibility` | Environment requirements |

## Tool-Providing Skills

Skills that add callable tools include a `tools.json` file alongside the SKILL.md:

```json
[
  {
    "name": "search",
    "description": "Search the web for current information.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "The search query"
        }
      },
      "required": ["query"]
    },
    "execution": {
      "type": "http",
      "method": "POST",
      "url": "https://api.example.com/search",
      "headers": {
        "Authorization": "Bearer ${SKILL_SEARCH_API_KEY}"
      }
    }
  }
]
```

### Execution Types

| Type | Description | Gated by |
|------|-------------|----------|
| `http` | Makes an HTTP request to the specified URL | Always available |
| `script` | Runs a local script in the skill directory | `ENABLE_SKILL_SCRIPTS=true` (disabled by default) |

### Environment Variable Substitution

Tool execution configs support `${SKILL_*}` variable references in URLs and headers. Only environment variables prefixed with `SKILL_` are resolved — this prevents skills from accessing secrets like `OPENAI_API_KEY` or `NEO4J_PASSWORD`.

```bash
# Set in your .env file
SKILL_SEARCH_API_KEY=your-search-api-key
SKILL_WEATHER_TOKEN=your-weather-token
```

### Tool Namespacing

Skill tools are namespaced to avoid collisions with built-in tools. A tool named `search` in skill `web-search` becomes `skill__web-search__search` in the agent's tool list. This is transparent to the user — the agent handles the mapping internally.

## Enabling and Disabling Skills

Installed skills are **disabled by default**. Toggle them on/off from the Settings page. Only enabled skills appear in the researcher agent's catalog.

When a skill is disabled:
- It no longer appears in the `<available_skills>` catalog
- The agent cannot activate it
- No prompt tokens are consumed

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_SKILLS` | `true` | Master switch for the skills system |
| `SKILLS_DIR` | `.agents/skills` | Directory for skill discovery (relative to project root or absolute) |
| `ENABLE_SKILL_SCRIPTS` | `false` | Allow skills to execute local scripts. **Security-sensitive** — only enable if you trust all installed skills. |
| `SKILL_SCRIPT_TIMEOUT` | `30` | Timeout in seconds for script execution |
| `SKILL_HTTP_TIMEOUT` | `15` | Timeout in seconds for HTTP tool calls |
| `MAX_SKILL_TOOLS` | `10` | Maximum total skill-provided tools in the researcher agent |
| `MAX_SKILL_INSTRUCTIONS_TOKENS` | `4000` | Approximate token budget for activated skill instructions |

## API Endpoints

All skill endpoints require **Admin** authentication.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/admin/skills` | List all installed skills |
| `GET` | `/api/admin/skills/{skill_id}` | Skill details (includes SKILL.md body and tools config) |
| `POST` | `/api/admin/skills/install` | Install from URL or registry. Body: `{url?}` or `{registry_id?}` |
| `PATCH` | `/api/admin/skills/{skill_id}` | Enable/disable. Body: `{enabled: true}` |
| `DELETE` | `/api/admin/skills/{skill_id}` | Uninstall skill and delete files |
| `GET` | `/api/admin/skills/registry/search` | Search skills.sh. Query: `q` |
| `POST` | `/api/admin/skills/discover` | Re-scan local skills directory |

## Security Considerations

- **Script execution is disabled by default.** The `ENABLE_SKILL_SCRIPTS` flag must be explicitly set to `true`. Only enable this if you trust all installed skills.
- **Environment variable sandboxing.** Only `SKILL_*` prefixed variables are accessible in tool execution configs.
- **Admin-only management.** All skill installation and configuration endpoints require admin authentication.
- **Tool count cap.** `MAX_SKILL_TOOLS` prevents excessive tool injection from degrading agent performance.
- **Result size cap.** Skill tool results are capped at 4000 characters before injection into agent context.
- **Prompt injection defense.** Anti-injection instructions are appended after skill content in the system prompt.

## Troubleshooting

**Skills not appearing in the agent?**
- Ensure the skill is **enabled** (toggled on in Settings)
- Ensure `ENABLE_SKILLS=true` in your environment
- Check that the SKILL.md has a valid `description` field — skills without descriptions are skipped

**Skill tool calls failing?**
- Check the backend logs for HTTP errors or timeouts
- Verify that `SKILL_*` environment variables are set correctly
- For script-based tools, ensure `ENABLE_SKILL_SCRIPTS=true`

**Registry search returns no results?**
- The search uses the skills.sh API (`https://skills.sh/api/search`). If the API is unreachable, check network connectivity from the backend container.

**Installed skill disappeared after restart?**
- Skills are stored on the filesystem in `SKILLS_DIR`. In Docker, this directory must be mounted as a volume to persist across container restarts.
