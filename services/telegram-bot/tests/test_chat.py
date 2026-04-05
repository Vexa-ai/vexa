"""Tests for chat streaming and text processing."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_to_html_basic():
    """Test markdown to HTML conversion."""
    from bot import _to_html

    # Bold
    assert "<b>hello</b>" in _to_html("**hello**")
    # Italic
    assert "<i>world</i>" in _to_html("*world*")
    # Inline code
    assert "<code>foo</code>" in _to_html("`foo`")
    # Headers
    assert "<b>Title</b>" in _to_html("# Title")
    # HTML escaping
    assert "&lt;script&gt;" in _to_html("<script>")


def test_to_html_code_blocks():
    """Test code block conversion."""
    from bot import _to_html
    result = _to_html("```python\nprint('hi')\n```")
    assert "<pre>" in result
    assert "print" in result


def test_to_html_links():
    """Test link conversion."""
    from bot import _to_html
    result = _to_html("[click](https://example.com)")
    assert 'href="https://example.com"' in result
    assert "click" in result


def test_chunk_text_short():
    """Short text returns single chunk."""
    from bot import _chunk_text
    chunks = _chunk_text("Hello world")
    assert len(chunks) == 1
    assert chunks[0] == "Hello world"


def test_chunk_text_long():
    """Long text is split at paragraph boundaries."""
    from bot import _chunk_text
    text = ("A" * 3000) + "\n\n" + ("B" * 3000)
    chunks = _chunk_text(text, limit=4000)
    assert len(chunks) == 2
    assert chunks[0].startswith("A")
    assert chunks[1].startswith("B")


def test_chunk_text_no_paragraph_break():
    """Falls back to newline, then space, then hard cut."""
    from bot import _chunk_text
    # No paragraph breaks — fall back to newline
    text = ("A" * 2000) + "\n" + ("B" * 3000)
    chunks = _chunk_text(text, limit=3000)
    assert len(chunks) >= 2


def test_truncate_short():
    """Short text passes through."""
    from bot import _truncate
    assert _truncate("hi") == "hi"


def test_truncate_long():
    """Long text gets truncated with indicator."""
    from bot import _truncate
    text = "A" * 5000
    result = _truncate(text, limit=100)
    assert len(result) < 200
    assert "[truncated]" in result


def test_format_activity():
    """Tool activity formatting."""
    from bot import _format_activity
    assert "Reading" in _format_activity("Read", "file.py")
    assert "file.py" in _format_activity("Read", "file.py")
    assert "Running command" in _format_activity("Bash", "ls -la")


def test_format_activity_long_summary():
    """Long summaries get truncated."""
    from bot import _format_activity
    result = _format_activity("Read", "x" * 100)
    assert "\u2026" in result
    assert len(result) < 80


def test_parse_meeting_url_google_meet():
    """Parse Google Meet URLs."""
    from bot import _parse_meeting_url
    result = _parse_meeting_url("https://meet.google.com/abc-defg-hij")
    assert result == ("google_meet", "abc-defg-hij")


def test_parse_meeting_url_teams():
    """Parse Teams URLs."""
    from bot import _parse_meeting_url
    url = "https://teams.microsoft.com/l/meetup-join/abc123"
    result = _parse_meeting_url(url)
    assert result[0] == "microsoft_teams"
    assert result[1] == url


def test_parse_meeting_url_zoom():
    """Parse Zoom URLs."""
    from bot import _parse_meeting_url
    result = _parse_meeting_url("https://zoom.us/j/123456789")
    assert result == ("zoom", "123456789")


def test_parse_meeting_url_unknown():
    """Unknown URLs return None."""
    from bot import _parse_meeting_url
    assert _parse_meeting_url("https://example.com/meeting") is None


@pytest.mark.asyncio
async def test_handle_message_sends_stream(mock_update, mock_context):
    """Test that plain text messages start a stream."""
    mock_update.message.text = "Hello agent"

    with patch("bot.get_or_create_auth", AsyncMock(return_value=("42", "tok_test"))):
        with patch("bot._start_stream", AsyncMock()) as mock_stream:
            from bot import handle_message
            await handle_message(mock_update, mock_context)
            mock_stream.assert_awaited_once()
            # Check message was passed
            assert mock_stream.call_args[0][3] == "Hello agent"


@pytest.mark.asyncio
async def test_handle_message_empty_text(mock_update, mock_context):
    """Empty messages get rejected."""
    mock_update.message.text = "   "

    from bot import handle_message
    await handle_message(mock_update, mock_context)
    mock_update.message.reply_text.assert_awaited_once()
    assert "text message" in mock_update.message.reply_text.call_args[0][0].lower()
