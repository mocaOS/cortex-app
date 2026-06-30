"""Unit tests for skill_service pure helpers.

Covers SKILL.md frontmatter parsing, skill-ID sanitization, and the
SKILL_-prefixed env-var substitution security boundary. (The catalog TTL cache
is covered by test_prompt_cache; install/activation needs Neo4j + network.)
"""

from __future__ import annotations

import pytest

from app.services.skill_service import (
    _extract_json_object,
    _parse_skill_md_from_string,
    _sanitize_skill_id,
    _substitute_env_vars,
)


# --- _sanitize_skill_id ------------------------------------------------------

@pytest.mark.parametrize(
    "name,expected",
    [
        ("My Cool Skill!", "my-cool-skill"),
        ("already-good", "already-good"),
        ("a___b   c", "a-b-c"),
        ("--Trim--", "trim"),
        ("UPPER", "upper"),
        ("", "unnamed-skill"),
        ("!!!", "unnamed-skill"),
    ],
)
def test_sanitize_skill_id(name, expected):
    assert _sanitize_skill_id(name) == expected


# --- _parse_skill_md_from_string ---------------------------------------------

def test_parse_skill_md_valid():
    md = (
        "---\n"
        "name: my-skill\n"
        "description: Does a thing\n"
        "metadata:\n"
        "  author: alice\n"
        "  version: 1.2\n"
        "---\n"
        "# Body\nInstructions here."
    )
    out = _parse_skill_md_from_string(md)
    assert out["name"] == "my-skill"
    assert out["description"] == "Does a thing"
    assert out["author"] == "alice" and out["version"] == 1.2
    assert out["body"] == "# Body\nInstructions here."


def test_parse_skill_md_missing_frontmatter_returns_none():
    assert _parse_skill_md_from_string("no frontmatter here") is None


def test_parse_skill_md_missing_description_returns_none():
    md = "---\nname: x\n---\nbody"
    assert _parse_skill_md_from_string(md) is None


def test_parse_skill_md_non_dict_frontmatter_returns_none():
    md = "---\n- just\n- a\n- list\n---\nbody"
    assert _parse_skill_md_from_string(md) is None


# --- _substitute_env_vars (security boundary) --------------------------------

def test_substitute_env_vars_resolves_skill_prefixed(monkeypatch):
    monkeypatch.setenv("SKILL_TOKEN", "abc123")
    assert _substitute_env_vars("Bearer ${SKILL_TOKEN}") == "Bearer abc123"


def test_substitute_env_vars_ignores_non_skill_vars(monkeypatch):
    """Security: only SKILL_-prefixed env vars may be interpolated."""
    monkeypatch.setenv("HOME_SECRET", "leak")
    assert _substitute_env_vars("x=${HOME_SECRET}") == "x=${HOME_SECRET}"


def test_substitute_env_vars_missing_skill_var_becomes_empty(monkeypatch):
    monkeypatch.delenv("SKILL_ABSENT", raising=False)
    assert _substitute_env_vars("v=${SKILL_ABSENT}") == "v="


# --- _extract_json_object (skill config analysis robustness) -----------------

def test_extract_json_object_plain():
    assert _extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_strips_markdown_fence():
    assert _extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_object_recovers_from_surrounding_prose():
    """Small models often wrap JSON in prose; we recover the embedded object."""
    raw = 'Here is the config:\n{"base_url": null, "variables": []}\nHope this helps!'
    assert _extract_json_object(raw) == {"base_url": None, "variables": []}


def test_extract_json_object_accepts_bare_array():
    """Back-compat: the older analysis prompt returned a bare array."""
    assert _extract_json_object('[{"name": "X"}]') == [{"name": "X"}]


def test_extract_json_object_returns_none_on_garbage():
    assert _extract_json_object("no json at all") is None


def test_extract_json_object_returns_none_on_truncated():
    """A truncated/unterminated response must not raise — caller retries."""
    assert _extract_json_object('{"a": "unterminated') is None


def test_extract_json_object_handles_empty_and_none():
    assert _extract_json_object("") is None
    assert _extract_json_object(None) is None
