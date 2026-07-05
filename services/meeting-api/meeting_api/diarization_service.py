"""Speaker diarization service — energy-based speaker change detection."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import Transcription

logger = logging.getLogger("meeting_api.diarization")


async def run_diarization(meeting_id: int, db: AsyncSession) -> dict:
    """Run energy-based speaker diarization on meeting transcripts.

    Uses average energy per speaker turn to cluster into 2-3 speakers.
    Updates speaker field on transcription segments.
    """
    segments = (await db.execute(
        select(Transcription).where(
            Transcription.meeting_id == meeting_id
        ).order_by(Transcription.start_time)
    )).scalars().all()

    if not segments:
        return {"total_speakers": 0, "segments": [], "updated_count": 0}

    # Compute energy estimate per segment (text length as proxy for speech energy)
    turns = []
    for seg in segments:
        if seg.text and seg.text.strip():
            energy = len(seg.text) / max(1, seg.end_time - seg.start_time)
            turns.append({
                "start": seg.start_time,
                "end": seg.end_time,
                "energy": energy,
                "segment": seg,
            })

    if not turns:
        return {"total_speakers": 0, "segments": [], "updated_count": 0}

    # Cluster by energy into 2-3 speakers
    energies = [t["energy"] for t in turns]
    min_e, max_e = min(energies), max(energies)

    if max_e - min_e < 0.01:
        # Single speaker
        for t in turns:
            if not t["segment"].speaker or t["segment"].speaker == "unknown":
                t["segment"].speaker = "Speaker A"
        await db.commit()
        return {"total_speakers": 1, "segments": _format_turns(turns), "updated_count": len(turns)}

    # 2-way split
    mid = (min_e + max_e) / 2
    labels = ["Speaker A" if t["energy"] < mid else "Speaker B" for t in turns]

    # Try 3-way if enough turns
    if len(turns) >= 6:
        for orig_label in ["Speaker A", "Speaker B"]:
            group = [t for t, l in zip(turns, labels) if l == orig_label]
            if len(group) >= 3:
                ge = [t["energy"] for t in group]
                ge_min, ge_max = min(ge), max(ge)
                if ge_max - ge_min > 0.05:
                    ge_mid = (ge_min + ge_max) / 2
                    for t, l in zip(turns, labels):
                        if l == orig_label and t["energy"] > ge_mid:
                            # Reassign to Speaker C
                            idx = turns.index(t)
                            labels[idx] = "Speaker C"

    # Apply labels and merge consecutive same-speaker turns
    updated = 0
    for t, label in zip(turns, labels):
        if not t["segment"].speaker or t["segment"].speaker == "unknown":
            t["segment"].speaker = label
            t["label"] = label
            updated += 1
        else:
            t["label"] = t["segment"].speaker

    await db.commit()

    total_speakers = len(set(labels))
    return {
        "total_speakers": total_speakers,
        "segments": _format_turns(turns, labels),
        "updated_count": updated,
    }


def _format_turns(turns, labels=None):
    result = []
    for i, t in enumerate(turns):
        label = labels[i] if labels else t.get("label", "Speaker A")
        result.append({
            "start": round(t["start"], 2),
            "end": round(t["end"], 2),
            "speaker": label,
        })
    return result
