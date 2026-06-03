"""GitLab provider (gitlab.com and self-hosted via base_url).

GitLab differs from GitHub/Gitea in vocabulary and shape:
  - repositories are **projects**, addressed by URL-encoded `namespace/project`
  - pull requests are **merge requests**, identified by project-scoped `iid`
  - the clone user is the literal `oauth2`
  - multi-file commits are a single atomic `actions[]` payload (no blob-sha juggling)
Wiki is API-based (`wiki_clone_url` stays None).
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from .base import GitProvider, GitProviderError, GitRepoRef, GitWriteResult, VerifyResult, WikiPage


class GitLabProvider(GitProvider):
    vendor = "gitlab"
    default_host = "gitlab.com"
    api_path = "/api/v4"

    def _clone_userinfo(self) -> str:
        return f"oauth2:{self._token}"

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _pid(owner: str, name: str) -> str:
        """URL-encoded project id (`namespace/project`)."""
        return quote(f"{owner}/{name}", safe="")

    async def verify(self) -> VerifyResult:
        resp = await self._request("GET", "/user")
        return VerifyResult(valid=True, login=resp.json().get("username"))

    async def list_repos(self, page: int = 1) -> list[GitRepoRef]:
        resp = await self._request(
            "GET", "/projects",
            params={"membership": "true", "per_page": 100, "page": page, "order_by": "last_activity_at"},
        )
        out = []
        for r in resp.json():
            full = r.get("path_with_namespace", "")
            owner, _, name = full.rpartition("/")
            out.append(GitRepoRef(
                owner=owner or r.get("namespace", {}).get("full_path", ""),
                name=r.get("path", name),
                full_name=full,
                default_branch=r.get("default_branch"),
                private=r.get("visibility") != "public",
                web_url=r.get("web_url"),
            ))
        return out

    async def default_branch(self, owner: str, name: str) -> Optional[str]:
        resp = await self._request("GET", f"/projects/{self._pid(owner, name)}")
        return resp.json().get("default_branch")

    async def get_file_content(self, owner, name, path, ref=None):
        ref = ref or await self.default_branch(owner, name) or "main"
        enc_path = quote(path, safe="")
        resp = await self._request(
            "GET", f"/projects/{self._pid(owner, name)}/repository/files/{enc_path}/raw",
            params={"ref": ref},
        )
        return resp.text

    async def list_wiki_pages(self, owner: str, name: str) -> Optional[list[WikiPage]]:
        resp = await self._request(
            "GET", f"/projects/{self._pid(owner, name)}/wikis", params={"with_content": "true"}
        )
        return [
            WikiPage(slug=p.get("slug", p.get("title", "")), title=p.get("title", ""),
                     content=p.get("content", "") or "")
            for p in resp.json()
        ]

    # ----- write -------------------------------------------------------------

    async def create_branch(self, owner, name, new_branch, from_branch) -> None:
        await self._request(
            "POST", f"/projects/{self._pid(owner, name)}/repository/branches",
            params={"branch": new_branch, "ref": from_branch},
        )

    async def commit_files(self, owner, name, branch, files, message) -> None:
        # GitLab's actions[] payload requires the right verb per file ('update'
        # on a missing file fails, 'create' on an existing one fails — and one
        # bad action rejects the whole atomic commit). Probe each file once to
        # pick create vs update, then commit everything in a single call.
        actions = []
        for p, c in files:
            try:
                await self._request(
                    "GET",
                    f"/projects/{self._pid(owner, name)}/repository/files/{quote(p, safe='')}",
                    params={"ref": branch},
                )
                action = "update"
            except GitProviderError:
                action = "create"
            actions.append({"action": action, "file_path": p, "content": c})
        await self._request(
            "POST", f"/projects/{self._pid(owner, name)}/repository/commits",
            json={"branch": branch, "commit_message": message, "actions": actions},
        )

    async def open_pull_request(self, owner, name, head, base, title, body) -> GitWriteResult:
        resp = await self._request(
            "POST", f"/projects/{self._pid(owner, name)}/merge_requests",
            json={"source_branch": head, "target_branch": base, "title": title, "description": body},
        )
        data = resp.json()
        return GitWriteResult(branch=head, url=data.get("web_url"), number=data.get("iid"))

    async def comment(self, owner, name, number, body) -> GitWriteResult:
        resp = await self._request(
            "POST", f"/projects/{self._pid(owner, name)}/merge_requests/{number}/notes",
            json={"body": body},
        )
        return GitWriteResult(number=number, extra={"note_id": resp.json().get("id")})
