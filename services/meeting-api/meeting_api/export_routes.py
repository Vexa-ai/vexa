"""Meeting export routes — Markdown export endpoint."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import Meeting, Transcription

router = APIRouter()


async def export_meeting_markdown(meeting_id: int, db: AsyncSession) -> str | None:
    """Generate a Markdown document for a meeting."""
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        return None

    segments_result = await db.execute(
        select(Transcription).where(
            Transcription.meeting_id == meeting_id
        ).order_by(Transcription.start_time)
    )
    segments = segments_result.scalars().all()

    lines = [f"# {meeting.platform} Meeting", "", f"**ID:** {meeting.platform_specific_id or 'unknown'}", f"**Status:** {meeting.status}", f"**Started:** {meeting.start_time.isoformat() if meeting.start_time else 'unknown'}"]
    if meeting.end_time:
        lines.append(f"**Ended:** {meeting.end_time.isoformat()}")
    lines.append("")

    ai_notes = (meeting.data or {}).get("ai_notes")
    if ai_notes:
        lines.append("## Summary")
        lines.append(ai_notes.get("summary", "N/A"))
        lines.append("")
        if ai_notes.get("key_moments"):
            lines.append("## Key Moments")
            for i, m in enumerate(ai_notes["key_moments"], 1):
                lines.append(f"{i}. {m}")
            lines.append("")
        if ai_notes.get("decisions"):
            lines.append("## Decisions")
            for i, d in enumerate(ai_notes["decisions"], 1):
                lines.append(f"{i}. {d}")
            lines.append("")
        if ai_notes.get("action_items"):
            lines.append("## Action Items")
            for i, a in enumerate(ai_notes["action_items"], 1):
                desc = a if isinstance(a, str) else a.get("description", str(a))
                assignee = ""
                if isinstance(a, dict):
                    assignee = a.get("assignee", "")
                deadline = ""
                if isinstance(a, dict):
                    deadline = a.get("deadline", "")
                meta = ", ".join(x for x in [assignee, deadline] if x)
                if meta:
                    lines.append(f"- [ ] {desc} ({meta})")
                else:
                    lines.append(f"- [ ] {desc}")
            lines.append("")
        if ai_notes.get("unresolved"):
            lines.append("## Open Questions")
            for i, q in enumerate(ai_notes["unresolved"], 1):
                lines.append(f"{i}. {q}")
            lines.append("")

    lines.append("## Transcript")
    lines.append("")
    for seg in segments:
        ts = format_timestamp(seg.start_time)
        speaker = f" **{seg.speaker}**:" if seg.speaker else ":"
        lines.append(f"{ts}{speaker} {seg.text}")
    lines.append("")

    return "\n".join(lines)


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@router.get("/internal/meetings/{meeting_id}/export")
async def export_meeting(
    meeting_id: int,
    format: str = "md",
    db: AsyncSession = Depends(get_db),
):
    if format != "md":
        raise HTTPException(status_code=400, detail="Only 'md' format supported")

    md = await export_meeting_markdown(meeting_id, db)
    if md is None:
        raise HTTPException(status_code=404, detail=f"Meeting {meeting_id} not found")

    return Response(
        content=md,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f"attachment; filename=meeting-{meeting_id}.md",
        },
    )
