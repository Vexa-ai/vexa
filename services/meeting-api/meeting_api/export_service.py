"""Markdown export service — generate .md from meeting record."""

import datetime
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Meeting, Transcription

logger = logging.getLogger("meeting_api.export")


async def export_meeting_markdown(meeting_id: int, db: AsyncSession) -> Optional[str]:
    """Generate a Markdown document for a meeting.

    Returns the .md text or None if meeting not found.
    """
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        return None

    result = await db.execute(
        select(Transcription).where(
            Transcription.meeting_id == meeting_id
        ).order_by(Transcription.start_time)
    )
    segments = result.scalars().all()

    lines = []

    # Header
    lines.append(f"# Meeting: {meeting.platform} — {meeting.platform_specific_id or 'unknown'}")
    lines.append("")
    lines.append(f"- **Platform:** {meeting.platform}")
    if meeting.start_time:
        lines.append(f"- **Start:** {meeting.start_time.isoformat()}")
    if meeting.end_time:
        lines.append(f"- **End:** {meeting.end_time.isoformat()}")
    lines.append(f"- **Status:** {meeting.status}")
    lines.append(f"- **Exported:** {datetime.datetime.utcnow().isoformat()}")
    lines.append("")

    # Transcript
    if segments:
        lines.append("## Transcript")
        lines.append("")
        for seg in segments:
            ts = format_timestamp(seg.start_time)
            speaker = seg.speaker or "Unknown"
            lines.append(f"**{ts} [{speaker}]** {seg.text}")
            lines.append("")

        # Speaker summary
        speakers = {}
        for seg in segments:
            s = seg.speaker or "Unknown"
            speakers[s] = speakers.get(s, 0) + 1
        if len(speakers) > 1:
            lines.append("## Speakers")
            lines.append("")
            for name, count in sorted(speakers.items(), key=lambda x: -x[1]):
                lines.append(f"- **{name}**: {count} segments")
            lines.append("")
    else:
        lines.append("*No transcript available.*")
        lines.append("")

    # AI notes
    data = meeting.data or {}
    ai_notes = data.get("ai_notes")
    if ai_notes and isinstance(ai_notes, dict):
        lines.append("## AI Notes")
        lines.append("")
        if ai_notes.get("summary"):
            lines.append(f"**Summary:** {ai_notes['summary']}")
            lines.append("")
        if ai_notes.get("key_moments"):
            lines.append("**Key Moments:**")
            for km in ai_notes["key_moments"]:
                lines.append(f"- {km}")
            lines.append("")
        if ai_notes.get("decisions"):
            lines.append("**Decisions:**")
            for d in ai_notes["decisions"]:
                lines.append(f"- {d}")
            lines.append("")
        if ai_notes.get("action_items"):
            lines.append("**Action Items:**")
            for item in ai_notes["action_items"]:
                if isinstance(item, dict):
                    desc = item.get("description", "")
                    assignee = item.get("assignee", "")
                    lines.append(f"- [ ] {desc}" + (f" (@{assignee})" if assignee else ""))
                else:
                    lines.append(f"- [ ] {item}")
            lines.append("")
        if ai_notes.get("unresolved"):
            lines.append("**Unresolved Questions:**")
            for q in ai_notes["unresolved"]:
                lines.append(f"- {q}")
            lines.append("")

    return "\n".join(lines)


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
