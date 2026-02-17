"""
Configuration for the Ultravox Agent service.
"""
import os

# Ultravox API
ULTRAVOX_API_KEY = os.getenv("ULTRAVOX_API_KEY", "")
ULTRAVOX_API_URL = os.getenv("ULTRAVOX_API_URL", "https://api.ultravox.ai/api")
ULTRAVOX_MODEL = os.getenv("ULTRAVOX_MODEL", "fixie-ai/ultravox")
ULTRAVOX_VOICE = os.getenv("ULTRAVOX_VOICE", "Mark")
ULTRAVOX_TEMPERATURE = float(os.getenv("ULTRAVOX_TEMPERATURE", "0.4"))
ULTRAVOX_LANGUAGE_HINT = os.getenv("ULTRAVOX_LANGUAGE_HINT", "en")

# Service ports
WS_PORT = int(os.getenv("ULTRAVOX_AGENT_PORT", "9092"))
HEALTH_PORT = int(os.getenv("ULTRAVOX_AGENT_HEALTH_PORT", "9093"))

# Audio
INPUT_SAMPLE_RATE = int(os.getenv("ULTRAVOX_INPUT_SAMPLE_RATE", "16000"))
OUTPUT_SAMPLE_RATE = int(os.getenv("ULTRAVOX_OUTPUT_SAMPLE_RATE", "24000"))
CLIENT_BUFFER_SIZE_MS = int(os.getenv("ULTRAVOX_CLIENT_BUFFER_MS", "30000"))

# Vexa / OpenClaw integration
VEXA_API_URL = os.getenv("VEXA_API_URL", "http://api-gateway:8080")
OPENCLAW_WEBHOOK_URL = os.getenv("OPENCLAW_WEBHOOK_URL", "")

# Default system prompt
DEFAULT_SYSTEM_PROMPT = """You are Vexa, an AI meeting assistant. People can talk to you and you talk back.

PERSONALITY: Friendly, concise, professional. Acknowledge before acting. Explain results clearly.

TOOLS:
- trigger_agent: For complex tasks (research, docs, tasks). Backend has full transcript with speaker names.
- send_chat_message: Post in meeting chat (links, summaries, structured content).
- show_image: Display on camera feed (charts, screenshots).
- get_meeting_context: Fetch transcript with speaker names when you need "who said what".

BEHAVIOR: Only act when addressed. Acknowledge first. Explain results conversationally. Supplement voice with chat for details. Stay silent when unsure."""
