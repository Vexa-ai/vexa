"""Compatibility MCP adapter: repository import remains owned by agent-api."""
import json
from urllib.parse import quote


def register_workspace_import_tools(mcp, *, http, subject, guard, agent_api):
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
        status, body = http("POST", f"{agent_api}/api/workspace/import",
                            {"X-User-Id": subject()}, {"repo": repo, "ref": ref, "credential_workspace": credential_workspace or None})
        return json.dumps(body if status == 202 else {"status": "failed", "error_status": status, "detail": body})

    @mcp.tool()
    @guard
    def workspace_import_status(operation_id: str) -> str:
        """Read your repository import progress. Only completed confirms success.

        Return failures to the user. Poll this tool while queued/running; do not start
        another import or claim completion. The completed result names the workspace.
        """
        status, body = http("GET", f"{agent_api}/api/workspace/import/{quote(operation_id, safe='')}",
                            {"X-User-Id": subject()})
        return json.dumps(body if status == 200 else {"status": "failed", "error_status": status, "detail": body})
