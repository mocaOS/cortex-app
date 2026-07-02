"""Characterization tests for researcher prompt/tool construction.

Locks the building blocks the researcher loop assembles each iteration
(`get_researcher_prompt`, skill blocks, tool list ordering). The stable-prefix
work (researcher_stable_prompt) must keep these contracts: when it lands, the
iteration counter moves out of the system prompt and these tests are updated
deliberately alongside it.
"""

from __future__ import annotations

from datetime import date

from app.services.prompt_security import get_anti_injection_instruction
from app.services.research_prompts import (
    build_activated_skills_block,
    build_skill_catalog_block,
    get_researcher_prompt,
    get_tools_with_skill_activation,
)


# ---------------------------------------------------------------------------
# get_researcher_prompt
# ---------------------------------------------------------------------------

class TestResearcherPrompt:
    def test_speed_prompt_contains_date_and_iteration(self):
        prompt = get_researcher_prompt("speed", 0, 5)
        assert date.today().isoformat() in prompt
        assert "Iteration 1 of 5." in prompt

    def test_quality_prompt_contains_iteration_guidance(self):
        prompt = get_researcher_prompt("quality", 2, 8)
        assert "Iteration 3 of 8." in prompt
        assert "<iteration_guidance>" in prompt

    def test_prompt_differs_only_in_iteration_line_across_iterations(self):
        """The static body is identical across iterations — the property the
        stable-prefix refactor relies on (only the counter line changes)."""
        for mode, max_it in (("speed", 5), ("quality", 8)):
            p1 = get_researcher_prompt(mode, 0, max_it)
            p2 = get_researcher_prompt(mode, 3, max_it)
            diff = [
                (a, b)
                for a, b in zip(p1.split("\n"), p2.split("\n"))
                if a != b
            ]
            assert diff == [
                (f"Iteration 1 of {max_it}.", f"Iteration 4 of {max_it}.")
            ]


# ---------------------------------------------------------------------------
# Skill blocks
# ---------------------------------------------------------------------------

class TestSkillBlocks:
    def test_catalog_block_currently_empty(self):
        # Auto-activation made the catalog block obsolete; it returns "".
        assert build_skill_catalog_block([]) == ""
        assert build_skill_catalog_block(
            [{"name": "jira", "description": "x", "skill_id": "1"}]
        ) == ""

    def test_activated_block_empty_when_no_instructions(self):
        assert build_activated_skills_block("") == ""

    def test_activated_block_wraps_in_active_skills_tag(self):
        block = build_activated_skills_block("Call GET /api/tickets to list.")
        assert "<active_skills>" in block
        assert "</active_skills>" in block
        assert "Call GET /api/tickets to list." in block

    def test_activated_block_strips_auth_instruction_lines(self):
        instructions = (
            "Call GET /api/tickets.\n"
            "Replace api_token with your actual token before calling.\n"
            "Use limit=10."
        )
        block = build_activated_skills_block(instructions)
        assert "Call GET /api/tickets." in block
        assert "Use limit=10." in block
        assert "Replace api_token" not in block


# ---------------------------------------------------------------------------
# Tool list assembly
# ---------------------------------------------------------------------------

def _names(tools):
    return [t["function"]["name"] for t in tools]


class TestToolAssembly:
    def test_done_is_always_last(self):
        for mode in ("speed", "quality"):
            for has_skills in (False, True):
                tools = get_tools_with_skill_activation(
                    mode, has_skills=has_skills
                )
                assert _names(tools)[-1] == "done"

    def test_base_tools_unchanged_without_skills_or_git(self):
        from app.services.research_prompts import get_tools_for_mode

        for mode in ("speed", "quality"):
            assert _names(
                get_tools_with_skill_activation(mode)
            ) == _names(get_tools_for_mode(mode))

    def test_skills_add_http_request_and_reasoning_in_speed(self):
        names = _names(
            get_tools_with_skill_activation("speed", has_skills=True)
        )
        assert "http_request" in names
        assert "reasoning" in names
        assert names[0] == "reasoning"

    def test_git_adds_git_repo_tool(self):
        names = _names(
            get_tools_with_skill_activation("speed", has_git=True)
        )
        assert "git_repo" in names

    def test_quality_keeps_reasoning_first_with_skills(self):
        names = _names(
            get_tools_with_skill_activation("quality", has_skills=True)
        )
        assert names[0] == "reasoning"
        assert names.index("http_request") == 1


# ---------------------------------------------------------------------------
# Anti-injection instruction
# ---------------------------------------------------------------------------

class TestAntiInjection:
    def test_disabled_returns_empty(self):
        assert get_anti_injection_instruction(enabled=False) == ""

    def test_enabled_returns_nonempty_stable_text(self):
        a = get_anti_injection_instruction(enabled=True)
        b = get_anti_injection_instruction(enabled=True)
        assert a and a == b  # stable across calls (prompt-cache friendly)


# ---------------------------------------------------------------------------
# has_skills-conditional prompts + skill instruction budget (v-next)
# ---------------------------------------------------------------------------

class TestSkillConditionalPrompts:
    def test_skill_less_prompts_never_mention_skills(self):
        """On skill-less deployments http_request doesn't exist and the
        <active_skills> block is absent — the prompt must not reference either
        (a small model will hallucinate the tool call and burn an iteration)."""
        for mode in ("speed", "quality"):
            prompt = get_researcher_prompt(mode, 0, 5, has_skills=False)
            assert "http_request" not in prompt
            assert "active_skills" not in prompt
            assert "skill" not in prompt.lower()

    def test_skill_prompts_keep_skill_guidance(self):
        for mode in ("speed", "quality"):
            prompt = get_researcher_prompt(mode, 0, 5, has_skills=True)
            assert "http_request" in prompt
            assert "<active_skills>" in prompt

    def test_speed_skill_prompt_makes_followup_search_conditional(self):
        prompt = get_researcher_prompt("speed", 0, 5, has_skills=True)
        assert "ALSO needs knowledge-base context" in prompt

    def test_static_prompt_stable_per_configuration(self):
        from app.services.research_prompts import get_researcher_prompt_static

        for has_skills in (False, True):
            a = get_researcher_prompt_static("speed", 5, has_skills=has_skills)
            b = get_researcher_prompt_static("speed", 5, has_skills=has_skills)
            assert a == b
            assert "Iteration 1 of 5." not in a


class TestSkillInstructionBudget:
    def test_budget_enforced_with_marker(self):
        body = "A" * 10_000
        block = build_activated_skills_block(body, max_chars=1000)
        assert "[skill instructions truncated" in block
        # wrapped block = header + capped body + marker; body itself capped
        assert len(block) < 10_000

    def test_no_budget_means_no_truncation(self):
        body = "B" * 10_000
        block = build_activated_skills_block(body)
        assert "truncated" not in block
        assert "B" * 10_000 in block

    def test_small_body_untouched_by_budget(self):
        block = build_activated_skills_block("Call GET /x.", max_chars=1000)
        assert "Call GET /x." in block
        assert "truncated" not in block


class TestKnowledgeSearchEntitiesParam:
    def test_entities_param_declared_optional(self):
        from app.services.research_prompts import KNOWLEDGE_SEARCH_TOOL

        props = KNOWLEDGE_SEARCH_TOOL["function"]["parameters"]["properties"]
        assert "entities" in props
        assert KNOWLEDGE_SEARCH_TOOL["function"]["parameters"]["required"] == [
            "queries"
        ]
