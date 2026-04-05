"""Parse agent CLI stream-json output into SSE events.

Handles Claude CLI's stream-json format. Callers can replace this module
with their own parser for different agent CLIs.
"""


def parse_event(data: dict) -> list[dict]:
    """Convert a stream-json event into SSE-friendly events."""
    events = []
    msg_type = data.get("type", "")

    if msg_type == "assistant":
        content = data.get("message", {}).get("content", [])
        text_block_count = 0
        for block in content:
            if block.get("type") == "text":
                prefix = "\n\n" if text_block_count > 0 else ""
                events.append({"type": "text_delta", "text": prefix + block["text"]})
                text_block_count += 1
            elif block.get("type") == "tool_use":
                events.append({
                    "type": "tool_use",
                    "tool": block.get("name", "unknown"),
                    "summary": _summarize_tool(block.get("name", ""), block.get("input", {})),
                })

    elif msg_type == "content_block_delta":
        delta = data.get("delta", {})
        if delta.get("type") == "text_delta":
            events.append({"type": "text_delta", "text": delta.get("text", "")})

    elif msg_type == "result":
        if data.get("is_error") or data.get("subtype") == "error_during_execution":
            errors = data.get("errors", [])
            error_msg = errors[0] if errors else "Agent CLI error"
            events.append({"type": "error", "message": error_msg})
        events.append({
            "type": "done",
            "session_id": data.get("session_id"),
            "cost_usd": data.get("cost_usd"),
            "duration_ms": data.get("duration_ms"),
        })

    return events


def _summarize_tool(name: str, inp: dict) -> str:
    """Short human-readable summary of a tool invocation."""
    if name == "Read":
        return f"Reading: {inp.get('file_path', '?')}"
    if name == "Write":
        return f"Writing: {inp.get('file_path', '?')}"
    if name == "Edit":
        return f"Editing: {inp.get('file_path', '?')}"
    if name in ("Glob", "Grep"):
        return f"{name}: {inp.get('pattern', '?')}"
    if name == "Bash":
        cmd = inp.get("command", "?")
        return f"Running: {cmd[:60]}"
    if name == "WebSearch":
        return f"Searching: {inp.get('query', '?')}"
    if name == "WebFetch":
        return f"Fetching: {inp.get('url', '?')}"
    return name
