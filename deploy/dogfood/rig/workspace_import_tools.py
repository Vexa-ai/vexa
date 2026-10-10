"""Compatibility MCP adapter: repository import remains owned by agent-api.

One port, `git(uid, method, path, body=None)`: agent-api's git-backed routes as the caller (the
rig's `_agent_git`, which sends a person's own session through the gateway so the git store can act
for them at the credential broker, and keeps a delegated worker on the internal tier)."""
import json
from urllib.parse import quote


def register_workspace_import_tools(mcp, *, git, subject, guard):
    @mcp.tool()
    @guard
    def workspace_import(repo: str, ref: str = "main", credential_workspace: str = "") -> str:
        """Import a repository as a NEW independent workspace, preserving Personal.

        Use this for public and private URLs. Do not WebFetch a repository to test access.
        The server uses configured credentials, including its deploy key for GitHub HTTPS
        URLs. credential_workspace may name a workspace you own whose key is already registered.
        Never pass tokens in chat. Poll workspace_import_status with the returned
        operation_id; queued/running is not success. Files and Git history stay at root.
        """
        status, body = git(subject(), "POST", "/api/workspace/import",
                           {"repo": repo, "ref": ref, "credential_workspace": credential_workspace or None})
        return json.dumps(body if status == 202 else {"status": "failed", "error_status": status, "detail": body})

    @mcp.tool()
    @guard
    def workspace_import_status(operation_id: str) -> str:
        """Read your repository import progress. Only completed confirms success.

        Return failures to the user. Poll this tool while queued/running; do not start
        another import or claim completion. The completed result names the workspace.
        """
        status, body = git(subject(), "GET",
                           f"/api/workspace/import/{quote(operation_id, safe='')}/status")
        return json.dumps(body if status == 200 else {"status": "failed", "error_status": status, "detail": body})
