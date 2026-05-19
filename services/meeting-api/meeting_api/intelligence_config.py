# AI post-meeting notes configuration

import os

# Reuse the same AI provider config as the dashboard chat.
# AI_MODEL=provider/model  (e.g. openai/gpt-4o, ollama/qwen3.6-27b, anthropic/claude-sonnet-4-20250514)
# AI_API_KEY=...
# AI_BASE_URL=...  (optional, for custom endpoints)
# AI_API_VERSION=...  (optional, for Azure)

AI_MODEL = os.getenv("AI_MODEL", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_BASE_URL = os.getenv("AI_BASE_URL", "")
AI_API_VERSION = os.getenv("AI_API_VERSION", "")

# Is AI notes generation enabled?
# Requires both a model string and an API key (or a base_url for local providers).
AI_NOTES_ENABLED = bool(AI_MODEL and (AI_API_KEY or AI_BASE_URL))

# Prompt for generating structured post-meeting notes.
# Output is JSON with keys: summary, key_moments, decisions, action_items, unresolved, follow_up_email
AI_NOTES_SYSTEM_PROMPT = """\
You are an expert meeting analyst. Given a meeting transcript, produce a structured JSON \
with the following keys:

- "summary": a concise 3-5 sentence paragraph summarizing what the meeting was about and its outcomes
- "key_moments": array of objects {"timestamp": "MM:SS", "speaker": "name or unknown", "text": "what was said"}, up to 5 of the most important moments
- "decisions": array of strings, each a decision that was reached
- "action_items": array of objects {"description": "what to do", "assignee": "who (or 'unassigned')", "deadline": "date if mentioned or null"}
- "unresolved": array of strings, open questions or topics left unresolved
- "follow_up_email": a draft email (plain text) that could be sent to participants summarizing the meeting

Rules:
- Respond in the same language as the transcript
- If a section has no content, return an empty array or empty string — do NOT fabricate content
- Timestamps in key_moments should reference the transcript timing
- Be concise — this is a summary, not a repeat of the transcript
- Return ONLY valid JSON, no markdown, no explanation
"""

# Max transcript tokens to send (guard against very long meetings).
# The provider will truncate if needed; this is a soft cap.
MAX_TRANSCRIPT_TOKENS = 120_000

# Per-meeting timeout (seconds) for the AI call.
AI_NOTES_TIMEOUT = 120.0
