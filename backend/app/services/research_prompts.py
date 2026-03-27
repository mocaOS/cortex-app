"""
Prompt templates and tool definitions for the researcher/writer agent pipeline.

The researcher agent uses OpenAI function-calling to iteratively gather information
from the knowledge base. The writer then synthesizes gathered context into a final answer.

Two modes:
- Speed (chat): 2 iterations max, knowledge_search + done only
- Quality (deep research): 10 iterations max, all tools including reasoning
"""

from datetime import date
from typing import Literal, List


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
                }
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
            "Think through your research strategy before taking action. Call this BEFORE "
            "every other tool call to plan your next step. Reflect on what you've learned "
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
) -> List[dict]:
    """Get tools for the given mode, with skill activation tools if skills exist.

    - If has_skills: inserts activate_skill + list_skills at high priority
    - If activated_skill_tools: appends those before done
    - done always stays last
    """
    base = get_tools_for_mode(mode)

    if not has_skills:
        return base

    # Insert activation tools right after reasoning (quality) or at start (speed)
    # so the LLM sees them early in the tool list
    done = base[-1]  # always last
    core = base[:-1]

    if mode == "quality" and core and core[0]["function"]["name"] == "reasoning":
        # reasoning first, then activation tools, then search tools
        result = [core[0], ACTIVATE_SKILL_TOOL, LIST_SKILLS_TOOL] + core[1:]
    else:
        result = [ACTIVATE_SKILL_TOOL, LIST_SKILLS_TOOL] + core

    # Append activated skill tools (if any)
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

    catalog_text = "\n".join(lines)
    return f"""

<available_skills>
You have access to external skills that can be activated on demand. Review the list below and activate any that are relevant to the user's query using the activate_skill tool.

{catalog_text}
</available_skills>"""


def build_activated_skills_block(activated_instructions: str) -> str:
    """Wrap activated skill instruction bodies for the system prompt."""
    if not activated_instructions:
        return ""
    return f"""

<active_skills>
The following skills have been activated for this session. Follow their instructions when relevant.

{activated_instructions}
</active_skills>"""


# =============================================================================
# Researcher Prompts
# =============================================================================


def get_researcher_prompt(
    mode: Literal["speed", "quality"],
    iteration: int,
    max_iterations: int,
) -> str:
    """Get the researcher system prompt for the given mode and iteration."""
    if mode == "speed":
        return _get_speed_researcher_prompt(iteration, max_iterations)
    else:
        return _get_quality_researcher_prompt(iteration, max_iterations)


def _get_speed_researcher_prompt(iteration: int, max_iterations: int) -> str:
    today = date.today().isoformat()

    return f"""You are a research assistant that gathers information from a knowledge base to answer user questions. Your job is to select and execute the available tools to find relevant information — no free-form replies.

You will receive the conversation history between the user and an AI assistant, along with the user's latest question. Use the available tools to gather the information needed to answer it.

Today's date: {today}

You are on iteration {iteration + 1} of {max_iterations}. Act efficiently.
When finished gathering information, call the `done` tool. Never output text directly.

<goal>
Find the most relevant information to answer the user's question using the available tools.
Formulate targeted search queries — use keywords and entity names, not full sentences.
Call done once you have what you need.
</goal>

<instructions>
- Your knowledge may be outdated. Always search the knowledge base to ground your answer, even for seemingly basic facts.
- You get one knowledge_search call with up to 3 queries. Make them count — cover different angles of the question.
- If the first search returns enough, call done immediately. If critical information is missing, use your remaining iteration to refine.
- For simple questions (greetings, clarifications, opinions), call done immediately with a note that no search is needed.
- Default to knowledge_search when information is missing or uncertain.
- Do not invent tools. Do not output JSON. Only call tools.
</instructions>

<query_strategy>
Split your 3 queries to maximize coverage:
- Query 1: Core subject or entity name
- Query 2: Specific aspect the user is asking about
- Query 3: Related context or alternative phrasing

Example — User: "What are the features of GPT-5?"
→ knowledge_search(["GPT-5 features capabilities", "GPT-5 architecture improvements", "GPT-5 benchmarks performance"])

Example — User: "How does Polygon compare to Ethereum for DeFi?"
→ knowledge_search(["Polygon DeFi ecosystem", "Ethereum DeFi comparison", "Polygon Ethereum scalability fees"])
</query_strategy>

<mistakes_to_avoid>
1. Over-assuming — don't assume information exists or doesn't; just search
2. Verification loops — don't waste iterations verifying; search directly for what you need
3. Ignoring context — if conversation history already has the answer, call done
4. Overthinking — keep queries focused and specific
</mistakes_to_avoid>"""


def _get_quality_researcher_prompt(iteration: int, max_iterations: int) -> str:
    today = date.today().isoformat()

    return f"""You are a deep-research assistant that conducts thorough, multi-angle investigations using a knowledge base. Your job is to gather comprehensive information by iteratively searching, exploring, and cross-referencing — no free-form replies.

You will receive the conversation history between the user and an AI assistant, along with the user's latest question. Use the available tools to conduct exhaustive research.

Today's date: {today}

You are on iteration {iteration + 1} of {max_iterations}. Use every iteration wisely to build comprehensive coverage.
When finished, call the `done` tool. Never output text directly.

<goal>
Conduct deep, multi-angle research to gather exhaustive information for the user's question.
Follow an iterative reason-act loop: call `reasoning` before every other tool call to plan your next step, execute the tool, then `reasoning` again to reflect and decide the next step.
Finish with `done` only when you have comprehensive, multi-angle information.
</goal>

<research_strategy>
For any topic, investigate from multiple angles:
1. Core facts — What is it? Key definitions and overview.
2. Details and features — What are the specifics? Capabilities, components, mechanisms.
3. Relationships and connections — How does it relate to other entities?
4. Context and comparisons — How does it compare to alternatives?
5. Nuance and limitations — What are the caveats, critiques, or open questions?
6. Community themes — What broader topic clusters is it part of?

Start broad with knowledge_search, then narrow down. Use community_search for thematic context. Use entity_lookup when results mention specific entities worth exploring deeper.
</research_strategy>

<instructions>
- Your knowledge may be outdated. Always use the tools to ground answers.
- This is DEEP RESEARCH mode — be exhaustive. Don't stop after one or two searches.
- Aim for 3-5+ knowledge_search calls covering different angles. Each call gets up to 3 queries.
- Use community_search at least once to understand broader thematic context.
- Use entity_lookup when previous results mention key entities whose connections could add depth.
- Cross-reference information across searches. If results hint at more depth, follow up.
- Each reasoning call should reflect on what you've found and what gaps remain.
- Call done only after you've exhausted reasonable research avenues.
- Do not invent tools. Do not output JSON. Only call tools.
- You MUST call reasoning before every other tool call to plan your next step.
</instructions>

<iteration_guidance>
- Iterations 1-2: Broad knowledge_search + community_search for overview
- Iterations 3-5: Targeted knowledge_search following leads from initial results
- Iterations 6-8: entity_lookup for key entities, fill remaining gaps
- Iterations 8-10: Final cross-referencing, call done when comprehensive

If you have comprehensive coverage earlier, call done — don't pad with redundant searches.
</iteration_guidance>

<reasoning_format>
Open each reasoning call with a brief intent phrase:
- "The user wants to know about X. I'll start by searching for the core concepts."
- "From those results, Y seems important. I should explore that connection."
- "I have good coverage of A, B, and C. Let me check for limitations."
- "Comprehensive coverage achieved. Ready to wrap up."
</reasoning_format>

<mistakes_to_avoid>
1. Shallow research — don't stop after one or two searches; dig deeper from multiple angles
2. Over-assuming — don't assume things exist or don't; search for them
3. Missing perspectives — look for both supporting and critical viewpoints
4. Ignoring leads — if results mention interesting entities or connections, explore them
5. Premature done — don't call done until you've covered the topic thoroughly
6. Skipping reasoning — always call reasoning first
7. Redundant queries — don't repeat the same search terms; vary your angles
</mistakes_to_avoid>"""


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
    return f"""You are an expert research assistant providing accurate, helpful answers based on knowledge base results.

Guidelines:
1. Synthesize the reference material into a coherent, natural-sounding answer
2. Cite sources inline using [src_1], [src_2] notation when referencing specific information
3. Structure longer answers with clear sections when appropriate
4. Be precise and factual — avoid speculation beyond what the sources support
5. If the sources don't fully answer the question, explain what aspects you can address
6. When there is conversation history, continue that conversation naturally

Response Style:
- Write naturally as if you're an expert directly answering the question
- Never mention "context", "documents provided", "knowledge base", "knowledge graph", or similar phrases
- Never say "Based on the provided context" or "According to the documents"
- Present information confidently as expert knowledge
- Prefer specific facts over vague generalizations
- Connect related concepts naturally
- If sources conflict, acknowledge the discrepancy objectively
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


def get_writer_user_prompt(
    mode: Literal["speed", "quality"],
    formatted_sources: str,
    graph_context_str: str,
    question: str,
    researcher_summary: str = "",
    has_history: bool = False,
) -> str:
    """Get the writer user prompt for the given mode."""
    if mode == "quality":
        summary_section = ""
        if researcher_summary:
            summary_section = f"\n\n=== Research Summary ===\n{researcher_summary}"

        return f"""Provide a detailed, comprehensive answer to this question based on the research findings below.

=== Reference Material ===
{formatted_sources if formatted_sources else "No references available."}
{graph_context_str if graph_context_str else ""}
{summary_section}

### Question:
{question}

### Answer:"""

    else:  # speed
        if has_history:
            # Follow-up: keep question prominent, sources as supplementary
            if formatted_sources or graph_context_str:
                return f"""{question}

(Additional reference material if needed:
{formatted_sources if formatted_sources else ""}
{graph_context_str if graph_context_str else ""})"""
            else:
                return question
        else:
            return f"""Answer the following question. Use reference IDs like [src_1], [src_2] to cite specific information.

=== Reference Material ===
{formatted_sources if formatted_sources else "No references available."}
{graph_context_str if graph_context_str else ""}

### Question:
{question}

### Answer:"""
