"""Unit tests for the git provider abstraction (pure construction logic, no network)."""

from __future__ import annotations

import pytest

from app.services.git_providers import get_provider
from app.services.git_providers.github import GitHubProvider
from app.services.git_providers.gitlab import GitLabProvider
from app.services.git_providers.gitea import GiteaProvider


def test_factory_returns_correct_types():
    assert isinstance(get_provider("github", "t"), GitHubProvider)
    assert isinstance(get_provider("gitlab", "t"), GitLabProvider)
    assert isinstance(get_provider("gitea", "t"), GiteaProvider)


def test_factory_rejects_unknown_vendor():
    with pytest.raises(ValueError):
        get_provider("bitbucket", "t")


def test_github_api_root_and_clone_urls():
    p = GitHubProvider(token="ghp_secret", base_url=None)
    assert p.api_root == "https://api.github.com"
    assert p.host == "github.com"
    assert p.authenticated_clone_url("octocat", "Hello") == \
        "https://x-access-token:ghp_secret@github.com/octocat/Hello.git"
    # GitHub wiki is clone-based
    assert p.wiki_clone_url("octocat", "Hello") == \
        "https://x-access-token:ghp_secret@github.com/octocat/Hello.wiki.git"


def test_github_enterprise_base_url():
    p = GitHubProvider(token="t", base_url="https://ghe.corp.com")
    assert p.api_root == "https://ghe.corp.com/api/v3"
    assert p.host == "ghe.corp.com"


def test_gitlab_clone_user_and_project_id_encoding():
    p = GitLabProvider(token="glpat", base_url=None)
    assert p.api_root == "https://gitlab.com/api/v4"
    # clone user is the literal 'oauth2'
    assert p.authenticated_clone_url("group/sub", "repo") == \
        "https://oauth2:glpat@gitlab.com/group/sub/repo.git"
    # project id is the URL-encoded namespace path
    assert p._pid("group/sub", "repo") == "group%2Fsub%2Frepo"
    # GitLab has no git wiki repo (API-based)
    assert p.wiki_clone_url("group", "repo") is None


def test_gitea_self_hosted_and_token_clone():
    p = GiteaProvider(token="tok", base_url="https://git.example.com")
    assert p.api_root == "https://git.example.com/api/v1"
    assert p.authenticated_clone_url("o", "r") == "https://tok@git.example.com/o/r.git"
    assert p.wiki_clone_url("o", "r") is None


def test_tls_verification_respects_insecure_hosts():
    secure = GitLabProvider(token="t", base_url="https://git.internal", insecure_hosts=set())
    insecure = GitLabProvider(token="t", base_url="https://git.internal",
                              insecure_hosts={"git.internal"})
    assert secure._verify_tls is True
    assert insecure._verify_tls is False


def test_token_scrubbed_from_messages():
    p = GitHubProvider(token="ghp_topsecret")
    scrubbed = p._scrub("failed for ghp_topsecret while cloning")
    assert "ghp_topsecret" not in scrubbed
    assert "***" in scrubbed
