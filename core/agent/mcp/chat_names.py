"""Chat naming uses the same subject-owned session index as the UI."""
import json


def register(runtime):
    @runtime.mcp.tool()
    @runtime._anon_guard
    def chat_name(session: str, title: str) -> str:
        """Name the current chat with a concise 3–7 word task title once its objective is clear.
        Use the current chat session supplied in the turn context. Avoid raw prompts, secrets,
        generic names and status words. Human-chosen names cannot be overwritten.
        """
        status, data = runtime._http('POST', f'{runtime.AGENT_API}/api/chat/name',
            {'X-User-Id': runtime.me()}, {'session': session, 'title': title, 'source': 'agent'})
        return json.dumps(data if status == 200 else {'status': 'unavailable', 'http_status': status})
