"""The GitLab adapter of the git provider port (``shared.git_provider``).

A self-hosted GitLab, at the base URL the operator configured — a port and a relative URL root
(``https://git.example.com:8443/gitlab``) included — with an optional private CA. Projects live in
groups that nest (``it/ai/vexa/notes``, up to 20 subgroups).

Tokens: personal, project, group and OAuth access tokens all authenticate the same two ways — git
over https as ``oauth2:<token>`` (``GITLAB_USERNAME``), and the API with the token in the
``PRIVATE-TOKEN`` header, never in a URL. Creating in a group needs a token with the ``api`` scope
and at least the Developer role there (Maintainer when the group restricts project creation);
pushing needs ``write_repository``.

API calls: ``GET /api/v4/namespaces/<group path>`` resolves the group, ``POST /api/v4/projects``
creates the project in it, ``GET /api/v4/user`` checks a token. Redirects are refused.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
from typing import Optional

from shared.git_provider import (GITLAB, GITLAB_USERNAME, MAX_PATH_SEGMENTS, GitProvider, GitProviderError,
                                 error_message, redact, secret_of)


class GitLabProvider(GitProvider):
    kind = GITLAB
    max_path_segments = MAX_PATH_SEGMENTS
    nested_groups = True
    token_user = GITLAB_USERNAME

    def default_api_url(self) -> str:
        return f"{self.base_url}/api/v4"

    def _headers(self, token: str) -> dict:
        return {"PRIVATE-TOKEN": secret_of(token), "Accept": "application/json",
                "Content-Type": "application/json", "User-Agent": "vexa-agent"}

    def _where(self) -> str:
        return self.label if self.label != self.name else f"GitLab on {self.netloc()}"

    def _fail(self, exc: urllib.error.HTTPError, token: str, *, where: str = "") -> GitProviderError:
        detail = redact(error_message(exc), token)
        who = self._where()
        if exc.code in (401, 403):
            return GitProviderError(f"{who} rejected the token (HTTP {exc.code}): "
                                    f"{detail or 'check the token and its api scope'}",
                                    fault="token_rejected", provider=self.name)
        if exc.code == 404:
            return GitProviderError(f"{who}: {where or 'that'} was not found, or the token cannot see it",
                                    fault="namespace_not_found", provider=self.name)
        if 300 <= exc.code < 400:
            return GitProviderError(f"{who} answered with a redirect (HTTP {exc.code}); use the host's "
                                    f"canonical address as baseUrl", fault="redirect", provider=self.name)
        return GitProviderError(f"{who} failed (HTTP {exc.code}): {detail}".strip(), provider=self.name)

    def _unreachable(self, exc: urllib.error.URLError, token: str) -> GitProviderError:
        return GitProviderError(f"{self._where()} unreachable: {redact(str(exc.reason), token)}",
                                fault="unreachable", provider=self.name)

    def create_repo(self, token: str, name: str, private: bool, owner: Optional[str]) -> str:
        where = f"group '{owner}'" if owner else "your namespace"
        headers = self._headers(token)
        try:
            body: dict = {"name": name, "path": name, "visibility": "private" if private else "public"}
            if owner:
                ns = self.call("GET", f"{self.api_url}/namespaces/" + urllib.parse.quote(owner, safe=""), headers)
                if not isinstance(ns, dict) or not ns.get("id"):
                    raise GitProviderError(f"{self._where()}: {where} was not found",
                                           fault="namespace_not_found", provider=self.name)
                body["namespace_id"] = ns["id"]
            data = self.call("POST", f"{self.api_url}/projects", headers, body)
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 409):
                detail = redact(error_message(exc), token)
                if "taken" in detail:
                    raise GitProviderError(
                        f"a repository named '{name}' already exists in {where} on {self.netloc()} — pick "
                        f"another name, or pass its URL as remote_url to push into it",
                        fault="repo_exists", provider=self.name) from None
                raise GitProviderError(f"{self._where()} refused the project (HTTP {exc.code}): {detail}",
                                       provider=self.name) from None
            raise self._fail(exc, token, where=where) from None
        except urllib.error.URLError as exc:
            raise self._unreachable(exc, token) from None
        clone_url = data.get("http_url_to_repo") if isinstance(data, dict) else None
        if not clone_url:
            raise GitProviderError(f"{self._where()} returned no clone URL", provider=self.name)
        return clone_url

    def verify_token(self, token: str) -> str:
        try:
            data = self.call("GET", f"{self.api_url}/user", self._headers(token))
        except urllib.error.HTTPError as exc:
            raise self._fail(exc, token, where="the token's user") from None
        except urllib.error.URLError as exc:
            raise self._unreachable(exc, token) from None
        return str((data or {}).get("username") or "")

    def deploy_keys_url(self, path: str) -> Optional[str]:
        return f"{self.web_url(path)}/-/settings/repository#js-deploy-keys-settings" if path else None
