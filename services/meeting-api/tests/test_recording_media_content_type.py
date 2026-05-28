from meeting_api.recording_finalizer import _media_content_type as finalizer_media_content_type
from meeting_api.recordings import media_content_type


def test_audio_webm_is_served_as_audio_webm():
    assert media_content_type("audio", "webm") == "audio/webm"
    assert finalizer_media_content_type("audio", "webm") == "audio/webm"


def test_video_webm_is_served_as_video_webm():
    assert media_content_type("video", "webm") == "video/webm"
    assert finalizer_media_content_type("video", "webm") == "video/webm"


def test_wav_stays_audio_wav():
    assert media_content_type("audio", "wav") == "audio/wav"
    assert finalizer_media_content_type("audio", "wav") == "audio/wav"
