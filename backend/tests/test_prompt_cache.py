"""Tests for the prompt-cache discipline: static researcher prompt,
provider-specific cache_control, and the skill catalog TTL cache."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.services.reasoning_config import apply_cache_control
from app.services.research_prompts import (
    get_researcher_prompt,
    get_researcher_prompt_static,
)


# ---------------------------------------------------------------------------
# Static researcher prompt (researcher_stable_prompt)
# ---------------------------------------------------------------------------

class TestStaticResearcherPrompt:
    def test_static_prompt_has_no_iteration_counter(self):
        for mode, max_it in (("speed", 5), ("quality", 8)):
            static = get_researcher_prompt_static(mode, max_it)
            assert "Iteration 1 of" not in static
            assert "Iteration" not in static.split("<instructions>")[0]

    def test_static_prompt_is_full_prompt_minus_iteration_line(self):
        for mode, max_it in (("speed", 5), ("quality", 8)):
            full = get_researcher_prompt(mode, 0, max_it)
            static = get_researcher_prompt_static(mode, max_it)
            assert static == full.replace(f"Iteration 1 of {max_it}.\n", "")

    def test_static_prompt_stable_across_calls(self):
        a = get_researcher_prompt_static("quality", 8)
        b = get_researcher_prompt_static("quality", 8)
        assert a == b


# ---------------------------------------------------------------------------
# apply_cache_control
# ---------------------------------------------------------------------------

_MESSAGES = [
    {"role": "system", "content": "big stable prompt"},
    {"role": "user", "content": "question"},
]


class TestApplyCacheControl:
    def test_openrouter_anthropic_marks_system_message(self):
        out = apply_cache_control(
            _MESSAGES, "https://openrouter.ai/api/v1", "anthropic/claude-sonnet-4.6"
        )
        assert out[0]["content"] == [{
            "type": "text",
            "text": "big stable prompt",
            "cache_control": {"type": "ephemeral"},
        }]
        assert out[1] == _MESSAGES[1]
        # input not mutated
        assert _MESSAGES[0]["content"] == "big stable prompt"

    def test_openrouter_non_anthropic_untouched(self):
        out = apply_cache_control(
            _MESSAGES, "https://openrouter.ai/api/v1", "openai/gpt-5.1"
        )
        assert out is _MESSAGES

    def test_other_backends_untouched(self):
        for base in (
            "https://api.openai.com/v1",
            "https://api.venice.ai/api/v1",
            "http://localhost:8001/v1",
        ):
            assert apply_cache_control(_MESSAGES, base, "anthropic/claude-x") is _MESSAGES

    def test_only_first_system_message_converted(self):
        msgs = _MESSAGES + [{"role": "system", "content": "iteration note"}]
        out = apply_cache_control(
            msgs, "https://openrouter.ai/api/v1", "anthropic/claude-sonnet-4.6"
        )
        assert isinstance(out[0]["content"], list)
        assert out[2]["content"] == "iteration note"

    def test_flag_routes_through_prepare_call(self):
        from app.services.reasoning_config import ReasoningMode, _prepare_call

        settings = get_settings()
        settings.enable_prompt_cache_control = True
        try:
            merged, *_ = _prepare_call(
                "https://openrouter.ai/api/v1",
                "anthropic/claude-sonnet-4.6",
                ReasoningMode.parse("off"),
                None,
                {"messages": list(_MESSAGES)},
            )
            assert isinstance(merged["messages"][0]["content"], list)
        finally:
            settings.enable_prompt_cache_control = False

    def test_flag_off_prepare_call_untouched(self):
        from app.services.reasoning_config import ReasoningMode, _prepare_call

        merged, *_ = _prepare_call(
            "https://openrouter.ai/api/v1",
            "anthropic/claude-sonnet-4.6",
            ReasoningMode.parse("off"),
            None,
            {"messages": list(_MESSAGES)},
        )
        assert merged["messages"][0]["content"] == "big stable prompt"


# ---------------------------------------------------------------------------
# Skill catalog/activation TTL cache
# ---------------------------------------------------------------------------

class TestSkillCache:
    def _service(self, monkeypatch):
        from app.services.skill_service import SkillService

        svc = SkillService()
        neo4j = MagicMock()
        neo4j.get_enabled_skills.return_value = [
            {"skill_id": "jira", "name": "Jira", "description": "d",
             "skill_type": "instruction"}
        ]
        monkeypatch.setattr(svc, "_get_neo4j", lambda: neo4j)
        return svc, neo4j

    def test_catalog_cached_within_ttl(self, monkeypatch):
        svc, neo4j = self._service(monkeypatch)
        first = svc.get_skill_catalog()
        second = svc.get_skill_catalog()
        assert first == second
        assert neo4j.get_enabled_skills.call_count == 1

    def test_cache_invalidated_on_mutation(self, monkeypatch):
        svc, neo4j = self._service(monkeypatch)
        svc.get_skill_catalog()
        svc.invalidate_cache()
        svc.get_skill_catalog()
        assert neo4j.get_enabled_skills.call_count == 2

    def test_cache_expires_after_ttl(self, monkeypatch):
        svc, neo4j = self._service(monkeypatch)
        svc.get_skill_catalog()
        # age the cache entry past the TTL
        ts, catalog = svc._catalog_cache
        svc._catalog_cache = (ts - svc._CACHE_TTL_SECONDS - 1, catalog)
        svc.get_skill_catalog()
        assert neo4j.get_enabled_skills.call_count == 2

    def test_cached_copy_is_isolated(self, monkeypatch):
        svc, _ = self._service(monkeypatch)
        first = svc.get_skill_catalog()
        first[0]["name"] = "mutated"
        second = svc.get_skill_catalog()
        assert second[0]["name"] == "Jira"

    def test_update_skill_invalidates(self, monkeypatch):
        svc, neo4j = self._service(monkeypatch)
        neo4j.update_skill.return_value = {"skill_id": "jira"}
        monkeypatch.setattr(svc, "_skill_node_to_info", lambda node: node)
        svc.get_skill_catalog()
        svc.update_skill("jira", enabled=False)
        svc.get_skill_catalog()
        assert neo4j.get_enabled_skills.call_count == 2
