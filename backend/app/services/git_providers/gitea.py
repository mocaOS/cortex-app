"""Gitea provider (self-hosted via base_url; gitea.com as default host).

Gitea's REST API is broadly GitHub-shaped but lives under /api/v1, uses
`Authorization: token <pat>`, exposes a wiki API, and commits multiple files
through a single ChangeFiles call.
"""

from __future__ import annotations

import base64
from typing import Optional
from urllib.parse import quote

from .base import GitProvider, GitRepoRef, GitWriteResult, VerifyResult, WikiPage


class GiteaProvider(GitProvider):
    vendor = "gitea"
    default_host = "gitea.com"
    api_path = "/api/v1"

    def _clone_userinfo(self) -> str:
        return self._token

    def _auth_headers(self) -> dict:
        return {"Authorization": f"token {self._token}", "Accept": "application/json"}

    async def verify(self) -> VerifyResult:
        resp = await self._request("GET", "/user")
        return VerifyResult(valid=True, login=resp.json().get("login"))

    async def list_repos(self, page: int = 1) -> list[GitRepoRef]:
        resp = await self._request("GET", "/user/repos", params={"limit": 50, "page": page})
        out = []
        for r in resp.json():
            out.append(GitRepoRef(
                owner=(r.get("owner") or {}).get("login", ""),
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
        params = {"ref": ref} if ref else None
        # Encode each path segment ('/' stays a separator) so names with
        # '#', '?', spaces etc. survive the URL.
        enc_path = quote(path, safe="/")
        resp = await self._request("GET", f"/repos/{owner}/{name}/contents/{enc_path}", params=params)
        data = resp.json()
        if data.get("encoding") == "base64" and data.get("content"):
            return base64.b64decode(data["content"]).decode("utf-8", "replace")
        return data.get("content", "") or ""

    async def list_wiki_pages(self, owner: str, name: str) -> Optional[list[WikiPage]]:
        resp = await self._request("GET", f"/repos/{owner}/{name}/wiki/pages")
        pages = []
        for p in resp.json():
            title = p.get("title", "")
            page = await self._request(
                "GET", f"/repos/{owner}/{name}/wiki/page/{quote(title, safe='')}"
            )
            pdata = page.json()
            content = pdata.get("content_base64")
            if content:
                content = base64.b64decode(content).decode("utf-8", errors="replace")
            else:
                content = pdata.get("content", "") or ""
            pages.append(WikiPage(slug=p.get("sub_url", title), title=title, content=content))
        return pages

    # ----- write -------------------------------------------------------------

    async def create_branch(self, owner, name, new_branch, from_branch) -> None:
        await self._request(
            "POST", f"/repos/{owner}/{name}/branches",
            json={"new_branch_name": new_branch, "old_branch_name": from_branch},
        )

    async def commit_files(self, owner, name, branch, files, message) -> None:
        change_files = []
        for path, content in files:
            # Probe existence to choose create vs update + carry the sha.
            operation, sha = "create", None
            try:
                cur = await self._request(
                    "GET", f"/repos/{owner}/{name}/contents/{quote(path, safe='/')}",
                    params={"ref": branch},
                )
                sha = cur.json().get("sha")
                operation = "update"
            except Exception:
                pass
            entry = {"operation": operation, "path": path, "content": self._b64(content)}
            if sha:
                entry["sha"] = sha
            change_files.append(entry)
        await self._request(
            "POST", f"/repos/{owner}/{name}/contents",
            json={"files": change_files, "branch": branch, "message": message},
        )

    async def open_pull_request(self, owner, name, head, base, title, body) -> GitWriteResult:
        resp = await self._request(
            "POST", f"/repos/{owner}/{name}/pulls",
            json={"head": head, "base": base, "title": title, "body": body},
        )
        data = resp.json()
        return GitWriteResult(branch=head, url=data.get("html_url"), number=data.get("number"))

    async def comment(self, owner, name, number, body) -> GitWriteResult:
        resp = await self._request(
            "POST", f"/repos/{owner}/{name}/issues/{number}/comments", json={"body": body}
        )
        return GitWriteResult(url=resp.json().get("html_url"), number=number)
