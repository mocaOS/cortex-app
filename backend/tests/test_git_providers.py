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


# ----- request-shaping tests (mocked _request, no network) --------------------


class _FakeResp:
    def __init__(self, payload=None, text=""):
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def _capture_requests(provider, responses=None):
    """Replace provider._request with a recorder; `responses` maps (method, url-substring) → reply."""
    calls = []

    async def fake_request(method, path, *, json=None, params=None):
        calls.append({"method": method, "path": path, "json": json, "params": params})
        for (m, frag), reply in (responses or {}).items():
            if m == method and frag in path:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        return _FakeResp()

    provider._request = fake_request
    return calls


def test_github_file_paths_url_encoded():
    import asyncio
    p = GitHubProvider(token="t")
    calls = _capture_requests(p, {("GET", "/contents/"): _FakeResp({"content": "", "encoding": ""})})
    asyncio.run(p.get_file_content("o", "r", "docs/my guide#1.md", "main"))
    assert calls[0]["path"] == "/repos/o/r/contents/docs/my%20guide%231.md"


def test_gitea_file_paths_and_wiki_title_url_encoded():
    import asyncio
    p = GiteaProvider(token="t", base_url="https://git.example.com")
    calls = _capture_requests(p, {
        ("GET", "/contents/"): _FakeResp({"content": "", "encoding": ""}),
        ("GET", "/wiki/pages"): _FakeResp([{"title": "My Page?"}]),
        ("GET", "/wiki/page/"): _FakeResp({"content": "hi"}),
    })
    asyncio.run(p.get_file_content("o", "r", "a b/c#d.md"))
    assert calls[0]["path"] == "/repos/o/r/contents/a%20b/c%23d.md"
    asyncio.run(p.list_wiki_pages("o", "r"))
    wiki_calls = [c for c in calls if "/wiki/page/" in c["path"]]
    assert wiki_calls[0]["path"] == "/repos/o/r/wiki/page/My%20Page%3F"


def test_gitlab_commit_files_mixes_create_and_update():
    import asyncio
    from app.services.git_providers.base import GitProviderError
    p = GitLabProvider(token="t")
    # 'exists.md' probe succeeds; 'new.md' probe 404s → create
    calls = _capture_requests(p, {
        ("GET", "/repository/files/exists.md"): _FakeResp({"file_path": "exists.md"}),
        ("GET", "/repository/files/new.md"): GitProviderError("404"),
        ("POST", "/repository/commits"): _FakeResp({}),
    })
    asyncio.run(p.commit_files("g", "r", "branch",
                               [("exists.md", "a"), ("new.md", "b")], "msg"))
    commit = [c for c in calls if c["method"] == "POST"][-1]
    actions = {a["file_path"]: a["action"] for a in commit["json"]["actions"]}
    assert actions == {"exists.md": "update", "new.md": "create"}
