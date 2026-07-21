"""transcribe — deferred transcription from the recording (#525): the sealed
``POST /meetings/{meeting_id}/transcribe`` served from the recordings + collector seams."""
from .adapters import HttpSttTranscriber, master_audio_resolver
from .router import build_router
from .service import TranscribeFault, normalize_language, transcribe_meeting

__all__ = [
    "HttpSttTranscriber",
    "TranscribeFault",
    "build_router",
    "master_audio_resolver",
    "normalize_language",
    "transcribe_meeting",
]
