"""
Prompt templates and tool definitions for the researcher/writer agent pipeline.

The researcher agent uses OpenAI function-calling to iteratively gather information
from the knowledge base. The writer then synthesizes gathered context into a final answer.

Two modes:
- Speed (chat): 2 iterations max, knowledge_search + done only
- Quality (deep research): 10 iterations max, all tools including reasoning
"""

from datetime import date
from typing import Literal, List, Optional


# =============================================================================
# Tool Definitions (OpenAI function-calling format)
# =============================================================================

KNOWLEDGE_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "knowledge_search",
        "description": (
            "Search the knowledge base using hybrid retrieval (vector similarity + keyword "
            "matching + graph traversal) with cross-encoder reranking. Returns ranked source "
            "chunks and related graph context (entities, relationships). This is your primary "
            "information-gathering tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "1-3 search queries. Use keywords and entity names optimized for "
                        "retrieval, not full sentences. Cover different angles of the question "
                        "to maximize recall."
                    ),
                },
                "entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Named entities / proper nouns appearing in your queries (people, "
                        "organizations, products, systems). Used for graph traversal — "
                        "always include them when your queries mention specific entities."
                    ),
                },
            },
            "required": ["queries"],
        },
    },
}

COMMUNITY_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "community_search",
        "description": (
            "Search entity community summaries for high-level thematic context. Communities "
            "are clusters of related entities with AI-generated summaries. Use this when you "
            "need broader thematic understanding or when search results hint at larger topic "
            "areas worth exploring."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A keyword or topic query to search community summaries.",
                }
            },
            "required": ["query"],
        },
    },
}

ENTITY_LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "entity_lookup",
        "description": (
            "Look up specific entities by name to get their descriptions, types, and "
            "connection counts. Use this when you know an entity name from previous search "
            "results and want to explore its details and connections deeper."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Entity names to look up (max 5). Supports partial matching "
                        "(e.g., 'Poly' finds 'Polygon')."
                    ),
                }
            },
            "required": ["names"],
        },
    },
}

REASONING_TOOL = {
    "type": "function",
    "function": {
        "name": "reasoning",
        "description": (
            "Think through your research strategy. Call this TOGETHER WITH your next "
            "tool call in the same response (they execute in order) — never as the only "
            "call in a turn, that wastes an iteration. Reflect on what you've learned "
            "so far and what gaps remain. Keep it natural language."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": (
                        "Your reasoning about what to do next, what you've learned so far, "
                        "and what gaps remain. Do not reference tool names."
                    ),
                }
            },
            "required": ["thought"],
        },
    },
}

HTTP_REQUEST_TOOL = {
    "type": "function",
    "function": {
        "name": "http_request",
        "description": (
            "Make an HTTP request to an external API. Use this when an active "
            "skill describes an API endpoint to call. Authentication is handled "
            "automatically — just provide the method and URL. For large "
            "collections, request fewer items per call and paginate (e.g. "
            "?limit=, ?page=, ?per_page=) instead of fetching everything at once."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "description": "HTTP method.",
                },
                "url": {
                    "type": "string",
                    "description": "The full API URL to call.",
                },
                "body": {
                    "type": "string",
                    "description": "Optional request body (for POST/PUT/PATCH).",
                },
            },
            "required": ["method", "url"],
        },
    },
}

GIT_REPO_TOOL = {
    "type": "function",
    "function": {
        "name": "git_repo",
        "description": (
            "Read from or act on the connected git repository. "
            "Actions: 'read_file' fetches a file's current contents; "
            "'propose_change' opens a pull request with your edits; "
            "'comment' adds a comment to an existing pull request. "
            "Writes ALWAYS go onto a new branch and open a pull request for human "
            "review — they never push to the default branch. Write actions are only "
            "available when the connection is configured for read/write access. "
            "Authentication is handled automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read_file", "propose_change", "comment"],
                    "description": "The repository action to perform.",
                },
                "path": {
                    "type": "string",
                    "description": "Repo-relative file path (for read_file).",
                },
                "files": {
                    "type": "array",
                    "description": "Files to create or update (for propose_change).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
                "title": {
                    "type": "string",
                    "description": "Pull request title (for propose_change).",
                },
                "body": {
                    "type": "string",
                    "description": "PR description (propose_change) or comment text (comment).",
                },
                "commit_message": {
                    "type": "string",
                    "description": "Commit message (for propose_change).",
                },
                "pr_number": {
                    "type": "integer",
                    "description": "Pull request / merge request number (for comment).",
                },
            },
            "required": ["action"],
        },
    },
}

DONE_TOOL = {
    "type": "function",
    "function": {
        "name": "done",
        "description": (
            "Signal that research is complete and you have gathered sufficient information. "
            "It will be triggered automatically at max iterations, so if low on iterations, "
            "focus on gathering information instead of calling this."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "Brief summary of what you found and the key themes "
                        "the answer should cover."
                    ),
                }
            },
            "required": ["summary"],
        },
    },
}


def get_tools_for_mode(mode: Literal["speed", "quality"]) -> List[dict]:
    """Get the tool definitions available for the given mode."""
    if mode == "speed":
        return [KNOWLEDGE_SEARCH_TOOL, DONE_TOOL]
    else:  # quality
        return [
            REASONING_TOOL,
            KNOWLEDGE_SEARCH_TOOL,
            COMMUNITY_SEARCH_TOOL,
            ENTITY_LOOKUP_TOOL,
            DONE_TOOL,
        ]


# =============================================================================
# Skill Activation Tools (agentskills.io on-demand pattern)
# =============================================================================

ACTIVATE_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "activate_skill",
        "description": (
            "Activate an external skill to gain access to its instructions and tools. "
            "Check <available_skills> in your system prompt for the list. Only activate "
            "skills relevant to the user's query. Once activated, the skill's tools "
            "become callable and its instructions are loaded into context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "The skill name to activate (from <available_skills>)."
                    ),
                }
            },
            "required": ["name"],
        },
    },
}

LIST_SKILLS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": (
            "List all available external skills with descriptions and activation "
            "status. Use this to check what skills you can activate."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


def get_tools_with_skill_activation(
    mode: Literal["speed", "quality"],
    has_skills: bool = False,
    activated_skill_tools: List[dict] = None,
    has_git: bool = False,
) -> List[dict]:
    """Get tools for the given mode, adding http_request (skills) and/or git_repo.

    Skills are auto-activated at loop start, so the model just needs
    http_request to call APIs described in the skill instructions. git_repo is
    added whenever a git connection exists. When either external tool is active
    in speed mode, the reasoning tool is added so the model can process large
    responses before deciding the next step.
    """
    base = get_tools_for_mode(mode)

    if not has_skills and not has_git:
        return base

    done = base[-1]  # always last
    core = base[:-1]

    extra = []
    if has_skills:
        extra.append(HTTP_REQUEST_TOOL)
    if has_git:
        extra.append(GIT_REPO_TOOL)

    if mode == "quality" and core and core[0]["function"]["name"] == "reasoning":
        result = [core[0]] + extra + core[1:]
    else:
        result = [REASONING_TOOL] + extra + core

    # Append activated skill tools (if any — from tools.json, not used currently)
    if activated_skill_tools:
        result.extend(activated_skill_tools)

    result.append(done)
    return result


def build_skill_catalog_block(catalog: List[dict]) -> str:
    """Build the compact skill catalog for the system prompt.

    Only includes name + truncated description (tier 1) — tells the agent
    what skills exist so it can decide whether to activate them.
    """
    if not catalog:
        return ""

    lines = []
    for s in catalog:
        desc = s.get("description", "")
        # Truncate long descriptions to keep the catalog compact
        if len(desc) > 120:
            desc = desc[:117] + "..."
        skill_type = s.get("skill_type", "instruction")
        lines.append(f'- {s["name"]}: {desc} [type: {skill_type}]')

    # Skills are auto-activated — the catalog block is no longer needed.
    # The activated instructions block (build_activated_skills_block) injects
    # the full SKILL.md body with API docs.
    return ""


def build_activated_skills_block(
    activated_instructions: str,
    max_chars: Optional[int] = None,
) -> str:
    """Wrap activated skill instruction bodies for the system prompt.

    Strips auth-related lines from the SKILL.md body to prevent the model
    from thinking it needs to handle authentication itself.

    ``max_chars`` enforces the MAX_SKILL_INSTRUCTIONS_TOKENS budget (callers
    pass ~4 chars/token): oversized instruction blocks are cut with an
    explicit truncation marker instead of growing the system prompt without
    bound as more skills are enabled.
    """
    if not activated_instructions:
        return ""
    # Strip lines that mention token replacement — they confuse the model
    # into thinking it needs to provide the token manually
    auth_keywords = [
        "replace api_token", "replace `api_token`", "api_token with",
        "if you don't have the token", "ask the user to provide",
        "replace token", "your api token", "your_api_token",
        "actual token",
    ]
    cleaned = "\n".join(
        line for line in activated_instructions.split("\n")
        if not any(kw in line.lower() for kw in auth_keywords)
    )
    if max_chars is not None and max_chars > 0 and len(cleaned) > max_chars:
        cleaned = (
            cleaned[:max_chars]
            + "\n[skill instructions truncated — budget reached; "
            "MAX_SKILL_INSTRUCTIONS_TOKENS]"
        )
    return f"""

<active_skills>
The following skills are active and connected to live external systems. When the user's question relates to a skill, call http_request with the API endpoint described below. Authentication is pre-configured and injected automatically — do NOT worry about tokens, API keys, or authorization headers. Just call the URL.

{cleaned}
</active_skills>"""


# =============================================================================
# Researcher Prompts
# =============================================================================


def get_researcher_prompt(
    mode: Literal["speed", "quality"],
    iteration: int,
    max_iterations: int,
    has_skills: bool = True,
) -> str:
    """Get the researcher system prompt for the given mode and iteration.

    ``has_skills=False`` drops every skills/http_request reference — on
    skill-less deployments the tool doesn't exist and the <active_skills>
    block is absent, so mentioning them only invites hallucinated tool calls
    (which burn an iteration in the "Unknown tool" branch).
    """
    if mode == "speed":
        return _get_speed_researcher_prompt(iteration, max_iterations, has_skills)
    else:
        return _get_quality_researcher_prompt(iteration, max_iterations, has_skills)


def get_researcher_prompt_static(
    mode: Literal["speed", "quality"],
    max_iterations: int,
    has_skills: bool = True,
) -> str:
    """Iteration-free researcher prompt (researcher_stable_prompt mode).

    Byte-stable across loop iterations so providers can serve it from prefix
    cache; the iteration counter is delivered as a trailing system note
    instead (see _run_researcher_loop). Stability holds per configuration:
    the prompt only changes when skills are enabled/disabled, not per request.
    """
    full = get_researcher_prompt(mode, 0, max_iterations, has_skills)
    return full.replace(f"Iteration 1 of {max_iterations}.\n", "")


def _get_speed_researcher_prompt(
    iteration: int, max_iterations: int, has_skills: bool = True
) -> str:
    today = date.today().isoformat()

    if has_skills:
        role = (
            "You are a research assistant with access to a knowledge base "
            "and external skills (live API connections)."
        )
        instructions = """<instructions>
- You have active skills with live API access (see <active_skills>). On your FIRST call, if the user's question relates to an active skill, call http_request with the endpoint from the skill docs. Authentication is automatic.
- Then, only if the question ALSO needs knowledge-base context, use knowledge_search (up to 3 queries per call). If the skill API already answered the question (e.g. live data like weather, tickets, emails), call done — do not search the knowledge base for its own sake.
- For pure knowledge-base questions, call knowledge_search directly. Include an `entities` array with any named entities from your queries.
- Call done when you have enough information. Only call tools, never output text directly.
</instructions>"""
    else:
        role = "You are a research assistant with access to a knowledge base."
        instructions = """<instructions>
- ALWAYS call knowledge_search first — never answer from memory alone; the knowledge base is the source of truth. You get up to 3 queries per call — cover different angles. Include an `entities` array with any named entities from your queries.
- Call done when you have enough information. Only call tools, never output text directly.
</instructions>"""

    return f"""{role}

Today's date: {today}
Iteration {iteration + 1} of {max_iterations}.

{instructions}"""


def _get_quality_researcher_prompt(
    iteration: int, max_iterations: int, has_skills: bool = True
) -> str:
    today = date.today().isoformat()

    if has_skills:
        role = (
            "You are a deep-research assistant with access to a knowledge base "
            "and external skills (live API connections)."
        )
        skill_instruction = "\n- You have active skills with live API access (see <active_skills>). On iteration 1, if the user's question relates to an active skill, call http_request with the endpoint from the skill docs. Authentication is automatic."
        skill_guidance = (
            "\n- Iteration 1: If <active_skills> has a relevant skill, call its API via http_request"
            "\n- Iterations 2-4: Broad knowledge_search + community_search for overview"
        )
    else:
        role = "You are a deep-research assistant with access to a knowledge base."
        skill_instruction = ""
        skill_guidance = "\n- Iterations 1-4: Broad knowledge_search + community_search for overview"

    return f"""{role}

Today's date: {today}
Iteration {iteration + 1} of {max_iterations}.

<instructions>{skill_instruction}
- This is DEEP RESEARCH mode — be exhaustive. Aim for 3-5+ knowledge_search calls from different angles. You may issue SEVERAL tool calls in one response — parallel searches are executed concurrently and are much faster than one per turn.
- Include an `entities` array on knowledge_search calls listing the named entities in your queries.
- Use community_search at least once for thematic context.
- Use entity_lookup when results mention key entities worth exploring.
- Call reasoning together with your next tool call(s) in the same response — never alone, that wastes an iteration.
- Call done only after you've exhausted reasonable research avenues.
- Only call tools, never output text directly.
</instructions>

<iteration_guidance>{skill_guidance}
- Iterations 5-7: Targeted knowledge_search following leads
- Iterations 8-10: entity_lookup, fill gaps, call done
</iteration_guidance>"""


# =============================================================================
# Writer Prompts
# =============================================================================


def get_writer_system_prompt(
    mode: Literal["speed", "quality"],
    anti_injection: str = "",
) -> str:
    """Get the writer system prompt for the given mode."""
    if mode == "speed":
        return _get_speed_writer_prompt(anti_injection)
    else:
        return _get_quality_writer_prompt(anti_injection)


def _get_speed_writer_prompt(anti_injection: str) -> str:
    return f"""You are an expert assistant answering a user's question in a live chat. Be fast, direct, and concise.

Guidelines:
1. Lead with the answer in the first sentence — no preamble, no restating the question.
2. Keep it short: 1–3 sentences for simple/factoid questions; one tight paragraph or a few bullets for multi-part ones. Never pad to seem thorough.
3. Cite sources inline as [src_1], [src_2] for specific facts.
4. Use only what the sources support. If they don't cover the question, say so in one sentence — don't speculate.
5. No headings or section scaffolding unless the question genuinely needs a short list.
6. When there is conversation history, continue it naturally.

Response Style:
- Answer as a knowledgeable expert speaking directly to the user.
- Never mention "context", "documents provided", "knowledge base", "knowledge graph", or similar phrases; never say "Based on the provided context" or "According to the documents".
- State specific facts confidently; prefer them over vague generalizations.
- If sources conflict, note the discrepancy in a clause, not a section.
{anti_injection}"""


def _get_quality_writer_prompt(anti_injection: str) -> str:
    return f"""You are an expert research assistant that synthesizes knowledge base results into comprehensive, well-structured answers.

Guidelines:
1. Provide a comprehensive answer that addresses all aspects of the question
2. Organize complex answers with clear structure — use headings (## Heading), subheadings, and bullet points where appropriate
3. Cite sources using reference IDs: [src_1], [src_2], etc. Every factual statement must have at least one citation.
4. Highlight key findings and insights
5. Note any limitations or gaps if you cannot fully address the question
6. Connect related concepts naturally and coherently — draw connections between different sources
7. Be precise and factual in your statements
8. Include a brief concluding section that synthesizes the findings

Response Style:
- Write naturally as if you're an expert composing an in-depth research briefing
- Never mention "context", "provided documents", "knowledge base", "knowledge graph", or similar phrases
- Never say "Based on the provided context" or "According to the documents"
- Present information confidently as expert knowledge
- Maintain a neutral, authoritative tone with engaging narrative flow
- Expand on technical or complex topics to make them accessible
- If sources conflict, acknowledge the discrepancy and present both perspectives

Depth Expectations:
- This is a deep research response. Be thorough and comprehensive.
- Cover the topic from multiple angles: definitions, details, relationships, context, and nuances.
- Don't settle for surface-level summaries — provide analysis, insights, and connections.
- A well-researched answer is typically substantial, but depth matters more than length. Never pad with filler.

Formatting:
- Use ## headings and ### subheadings to structure longer answers
- Use bullet points or numbered lists for enumerations
- Use **bold** for key terms and emphasis
- Use Markdown formatting throughout
- Start directly with the content — no main title needed
{anti_injection}"""


def _format_failed_actions(failed_actions: Optional[List[str]]) -> str:
    """Render failed skill API calls as an explicit instruction the writer must
    relay, so a failed action (e.g. a ticket POST) is never silently dropped."""
    if not failed_actions:
        return ""
    items = "\n".join(f"- {a}" for a in failed_actions)
    return (
        "\n\n=== Failed Actions (MUST report to the user) ===\n"
        "The following action(s) were attempted on the user's behalf and FAILED. "
        "You MUST clearly tell the user that the action did not succeed and briefly "
        "why (use the error detail). Do NOT claim or imply the action completed.\n"
        f"{items}"
    )


def get_writer_user_prompt(
    mode: Literal["speed", "quality"],
    formatted_sources: str,
    graph_context_str: str,
    question: str,
    researcher_summary: str = "",
    has_history: bool = False,
    failed_actions: Optional[List[str]] = None,
) -> str:
    """Get the writer user prompt for the given mode."""
    failed_section = _format_failed_actions(failed_actions)

    if mode == "quality":
        summary_section = ""
        if researcher_summary:
            summary_section = f"\n\n=== Research Summary ===\n{researcher_summary}"

        return f"""Provide a detailed, comprehensive answer to this question based on the research findings below.

=== Reference Material ===
{formatted_sources if formatted_sources else "No references available."}
{graph_context_str if graph_context_str else ""}
{summary_section}{failed_section}

### Question:
{question}

### Answer:"""

    else:  # speed
        if has_history:
            # Follow-up: keep question prominent, sources as supplementary
            if formatted_sources or graph_context_str or failed_section:
                return f"""{question}

(Additional reference material if needed:
{formatted_sources if formatted_sources else ""}
{graph_context_str if graph_context_str else ""}){failed_section}"""
            else:
                return question
        else:
            return f"""Answer the following question. Use reference IDs like [src_1], [src_2] to cite specific information.

=== Reference Material ===
{formatted_sources if formatted_sources else "No references available."}
{graph_context_str if graph_context_str else ""}{failed_section}

### Question:
{question}

### Answer:"""
