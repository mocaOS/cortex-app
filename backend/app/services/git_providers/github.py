"""GitHub provider (github.com and GitHub Enterprise via base_url).

Wiki ingestion is clone-based — GitHub exposes no wiki REST API — so
`wiki_clone_url` returns the `<repo>.wiki.git` remote and `list_wiki_pages`
stays None.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from .base import GitProvider, GitRepoRef, GitWriteResult, VerifyResult


class GitHubProvider(GitProvider):
    vendor = "github"
    default_host = "github.com"
    api_path = "/api/v3"  # overridden below for github.com

    @property
    def api_root(self) -> str:
        # github.com uses the dedicated api.github.com host, not /api/v3.
        if not self._base_url:
            return "https://api.github.com"
        return f"{self._base_url}{self.api_path}"

    def _clone_userinfo(self) -> str:
        return f"x-access-token:{self._token}"

    def wiki_clone_url(self, owner: str, name: str) -> Optional[str]:
        return f"https://{self._clone_userinfo()}@{self.host}/{owner}/{name}.wiki.git"

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def verify(self) -> VerifyResult:
        resp = await self._request("GET", "/user")
        data = resp.json()
        return VerifyResult(valid=True, login=data.get("login"))

    async def list_repos(self, page: int = 1) -> list[GitRepoRef]:
        resp = await self._request(
            "GET", "/user/repos",
            params={"per_page": 100, "page": page, "sort": "updated", "affiliation": "owner,collaborator,organization_member"},
        )
        out = []
        for r in resp.json():
            owner = (r.get("owner") or {}).get("login", "")
            out.append(GitRepoRef(
                owner=owner,
                name=r.get("name", ""),
                full_name=r.get("full_name", ""),
                default_branch=r.get("default_branch"),
                private=bool(r.get("private")),
                web_url=r.get("html_url"),
            ))
        return out

    async def default_branch(self, owner: str, name: str) -> Optional[str]:
        resp = await self._request("GET", f"/repos/{owner}/{name}")
        return resp.json().get("default_branch")

    async def get_file_content(self, owner, name, path, ref=None):
        import base64
        params = {"ref": ref} if ref else None
        # Encode each path segment ('/' stays a separator) so names with
        # '#', '?', spaces etc. survive the URL.
        enc_path = quote(path, safe="/")
        resp = await self._request("GET", f"/repos/{owner}/{name}/contents/{enc_path}", params=params)
        data = resp.json()
        if data.get("encoding") == "base64" and data.get("content"):
            return base64.b64decode(data["content"]).decode("utf-8", "replace")
        return data.get("content", "") or ""

    # ----- write -------------------------------------------------------------

    async def create_branch(self, owner: str, name: str, new_branch: str, from_branch: str) -> None:
        ref = await self._request("GET", f"/repos/{owner}/{name}/git/ref/heads/{from_branch}")
        sha = ref.json()["object"]["sha"]
        await self._request(
            "POST", f"/repos/{owner}/{name}/git/refs",
            json={"ref": f"refs/heads/{new_branch}", "sha": sha},
        )

    async def commit_files(self, owner, name, branch, files, message) -> None:
        for path, content in files:
            enc_path = quote(path, safe="/")
            # Existing file → need its blob sha to update; 404 means create.
            existing_sha = None
            try:
                cur = await self._request(
                    "GET", f"/repos/{owner}/{name}/contents/{enc_path}", params={"ref": branch}
                )
                existing_sha = cur.json().get("sha")
            except Exception:
                pass
            payload = {"message": message, "content": self._b64(content), "branch": branch}
            if existing_sha:
                payload["sha"] = existing_sha
            await self._request("PUT", f"/repos/{owner}/{name}/contents/{enc_path}", json=payload)

    async def open_pull_request(self, owner, name, head, base, title, body) -> GitWriteResult:
        resp = await self._request(
            "POST", f"/repos/{owner}/{name}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )
        data = resp.json()
        return GitWriteResult(branch=head, url=data.get("html_url"), number=data.get("number"))

    async def comment(self, owner, name, number, body) -> GitWriteResult:
        resp = await self._request(
            "POST", f"/repos/{owner}/{name}/issues/{number}/comments", json={"body": body}
        )
        return GitWriteResult(url=resp.json().get("html_url"), number=number)
