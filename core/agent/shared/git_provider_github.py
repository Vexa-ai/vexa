"""The GitHub adapter of the git provider port (``shared.git_provider``).

The one place agent-api names GitHub's host. The built-in provider is github.com; an operator may
add a GitHub Enterprise Server as ``{"kind": "github", "baseUrl": "https://ghe.example"}`` (its API
is ``<baseUrl>/api/v3`` unless ``apiUrl`` says otherwise).

Creation is ``POST /user/repos`` (or ``/orgs/{org}/repos``) with the caller's token in an
``Authorization: Bearer`` header — byte-for-byte the request agent-api sent before the port existed
(``tests/fixtures/github_create_recorded.json``). Redirects are refused, as for every provider.
"""
from __future__ import annotations

import urllib.error
from typing import Optional

from shared.git_provider import GITHUB, GitProvider, GitProviderError, error_message, redact

#: GitHub's own address and API — the built-in provider's defaults.
GITHUB_BASE_URL = "https://github.com"
GITHUB_API_URL = "https://api.github.com"
GITHUB_HOST = "github.com"


class GitHubProvider(GitProvider):
    kind = GITHUB
    max_path_segments = 2
    nested_groups = False

    def default_api_url(self) -> str:
        return GITHUB_API_URL if self.base_url == GITHUB_BASE_URL else f"{self.base_url}/api/v3"

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "Content-Type": "application/json", "User-Agent": "vexa-agent"}

    def _fail(self, exc: urllib.error.HTTPError, token: str, *, creating: str = "") -> GitProviderError:
        detail = redact(error_message(exc), token)
        if exc.code in (401, 403):
            return GitProviderError(
                f"{self.label} rejected the token (HTTP {exc.code}): {detail or 'check the token and its repo scope'}",
                fault="token_rejected", provider=self.name)
        if 300 <= exc.code < 400:
            return GitProviderError(f"{self.label} answered with a redirect (HTTP {exc.code})",
                                    fault="redirect", provider=self.name)
        what = "repo creation" if creating else "token check"
        return GitProviderError(f"{self.label} {what} failed (HTTP {exc.code}): {detail}".strip(),
                                fault="failed", provider=self.name)

    def create_repo(self, token: str, name: str, private: bool, owner: Optional[str]) -> str:
        url = f"{self.api_url}/orgs/{owner}/repos" if owner else f"{self.api_url}/user/repos"
        try:
            data = self.call("POST", url, self._headers(token), {"name": name, "private": bool(private)})
        except urllib.error.HTTPError as exc:
            if exc.code == 422:
                where = f"org '{owner}'" if owner else "your account"
                raise GitProviderError(
                    f"a repository named '{name}' already exists under {where} — pick another name, "
                    f"or pass its URL as remote_url to push into it", fault="repo_exists", provider=self.name) from None
            raise self._fail(exc, token, creating=name) from None
        except urllib.error.URLError as exc:
            raise GitProviderError(f"{self.label} unreachable: {redact(str(exc.reason), token)}",
                                   fault="unreachable", provider=self.name) from None
        clone_url = data.get("clone_url") or data.get("html_url") if isinstance(data, dict) else None
        if not clone_url:
            raise GitProviderError(f"{self.label} repo creation returned no clone URL", provider=self.name)
        return clone_url

    def verify_token(self, token: str) -> str:
        try:
            data = self.call("GET", f"{self.api_url}/user", self._headers(token))
        except urllib.error.HTTPError as exc:
            raise self._fail(exc, token) from None
        except urllib.error.URLError as exc:
            raise GitProviderError(f"{self.label} unreachable: {redact(str(exc.reason), token)}",
                                   fault="unreachable", provider=self.name) from None
        return str((data or {}).get("login") or "")

    def deploy_keys_url(self, path: str) -> Optional[str]:
        return f"{self.web_url(path)}/settings/keys" if path and path.count("/") == 1 else None


def builtin_github() -> GitHubProvider:
    """The provider every deployment has: github.com, named ``github``, shown as ``GitHub``."""
    return GitHubProvider("github", GITHUB_BASE_URL, label="GitHub")

