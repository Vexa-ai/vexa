"""
Configuration settings for the WhisperLive server.

This file centralizes settings that control the server's audio processing,
transcription, and voice activity detection behavior. Modifying these values
allows for fine-tuning the server's performance and transcription latency.
"""

import os

# Audio Buffer Settings
# ---------------------
# These settings control the behavior of the server's audio buffer. The server
# temporarily stores incoming audio chunks in a buffer before sending them for
# transcription.

# Maximum size of the audio buffer in seconds. If the buffer grows beyond this
# duration, the oldest audio data is discarded to manage memory usage. This
# can prevent run-away memory consumption if a client sends audio indefinitely.
MAX_BUFFER_S = 120

# The duration of audio in seconds to discard from the beginning of the buffer
# when MAX_BUFFER_S is reached. This is done to make space for new audio data.
DISCARD_BUFFER_S = 30


# Forced Audio Clipping Settings
# ------------------------------
# These settings are a safeguard against scenarios where the voice activity
# detector (VAD) fails to detect speech segments for an extended period, which
# could cause the buffer to grow indefinitely.

# If no speech segment is detected by VAD for this duration (in seconds), the
# audio buffer is forcibly clipped. This acts as a fallback to ensure the
# transcription process doesn't stall.
CLIP_IF_NO_SEGMENT_S = 25

# When the audio buffer is clipped (due to CLIP_IF_NO_SEGMENT_S), this is the
# duration of audio (in seconds) to retain from the end of the buffer.
# This retained audio provides context for the subsequent transcription.
CLIP_RETAIN_S = 5


# Minimum Audio for Transcription
# -------------------------------
# This setting determines the minimum amount of audio required to trigger
# a transcription attempt.

# The minimum duration of audio (in seconds) that must be present in the buffer
# before it is sent to the transcription model. A smaller value can lead to
# lower latency but may result in less accurate, fragmented transcriptions.
# Set to 1.0s for low latency transcription.
MIN_AUDIO_S = 1
# Tier2 (deferred) defaults: larger windows for lower service pressure.
MIN_AUDIO_S_TIER2 = 20

# Minimum Time Between Transcription Requests
# -------------------------------------------
# This setting limits how frequently the transcription service can be called
# per connection, preventing overload and ensuring fair resource usage.

# Minimum time (in seconds) between transcription requests per connection.
# This rate limit ensures we don't hammer the transcription service even if
# audio accumulates quickly. For example:
# - 0.1 = minimum 0.1 seconds between requests (max 10 requests per second)
# - 1.0 = minimum 1.0 seconds between requests (max 1 request per second)
# - 2.0 = minimum 2.0 seconds between requests (max 1 request per 2 seconds)
# - 10.0 = minimum 10.0 seconds between requests (max 1 request per 10 seconds)
# Set to 10.0 for max 1 request per 10 seconds (10s minimum between requests).
MIN_TIME_BETWEEN_REQUESTS_S = 0.5
# Tier2 (deferred) pacing default.
MIN_TIME_BETWEEN_REQUESTS_S_TIER2 = 20.0

# Durable Recording Spool Settings
# --------------------------------
# Persist incoming float32 audio frames to disk as they arrive so recordings
# survive bot/transcriber failures during the meeting.
WL_RECORDING_DIR = os.getenv("WL_RECORDING_DIR", "/tmp/wl-recordings")
WL_RECORDING_FLUSH_SECONDS = float(os.getenv("WL_RECORDING_FLUSH_SECONDS", "3"))
WL_RECORDING_FSYNC_SECONDS = float(os.getenv("WL_RECORDING_FSYNC_SECONDS", "10"))
WL_RECORDING_ROTATE_SECONDS = float(os.getenv("WL_RECORDING_ROTATE_SECONDS", "20"))
WL_RECORDING_ROTATE_BYTES = int(os.getenv("WL_RECORDING_ROTATE_BYTES", str(16 * 1024 * 1024)))
WL_RECORDING_SNAPSHOT_SECONDS = float(os.getenv("WL_RECORDING_SNAPSHOT_SECONDS", "20"))


# Voice Activity Detection (VAD) Settings
# ---------------------------------------
# These settings configure the Voice Activity Detector, which is responsible for
# identifying speech in the audio stream.

# The VAD onset threshold. This value (between 0 and 1) determines how sensitive
# the VAD is. A higher value requires a more confident prediction of speech to
# trigger the VAD. Adjust this if the VAD is too sensitive to background noise
# or not sensitive enough to quiet speech.
VAD_ONSET = 0.5

# The threshold for the VAD to decide that there is no speech in an audio
# chunk. This is used by the Whisper model's internal VAD.
VAD_NO_SPEECH_THRESH = 0.9

# Maximum duration of a speech segment in seconds before it is split. If None,
# the value will be set to the model's chunk_length (typically 30 seconds for
# Whisper models). Setting a smaller value will create shorter segments, which
# can improve latency but may reduce transcription quality for long utterances.
VAD_MAX_SPEECH_DURATION_S = None

# Minimum duration of silence in milliseconds required to split segments. A
# smaller value will split segments more aggressively on shorter pauses, while
# a larger value requires longer silences before splitting. Default is 160ms.
# This affects how VAD breaks continuous speech into segments for transcription.
VAD_MIN_SILENCE_DURATION_MS = 2


# Transcription Output Management
# -------------------------------
# These settings control how the transcribed text is managed and sent to the client.

# If the transcription model produces the exact same output this many times in a
# row, it's considered to be "stuck." This is a safeguard to prevent repetitive
# outputs under certain conditions.
# Lower values (3-5) reduce latency by reconfirming segments faster.
# Set to 3 for low latency transcription.
SAME_OUTPUT_THRESHOLD = 5
# Tier2 (deferred) requires fewer reconfirmations before cursor advance.
SAME_OUTPUT_THRESHOLD_TIER2 = 2

# If there's a pause in speech (i.e., Whisper produces no new text), the server
# will continue to send the previously transcribed segments to the client for this
# duration (in seconds). This creates a more stable output for the user.
SHOW_PREV_OUT_THRESH_S = 5

# If there has been no speech for this duration (in seconds), an empty string is
# added to the transcript. This helps to visually represent a pause in the
# conversation.
ADD_PAUSE_THRESH_S = 3


# Transcription Model Settings
# ----------------------------
# These settings control the behavior of the Whisper transcription model.

# Beam size for decoding. A smaller value (1) uses greedy decoding for faster
# processing, while larger values (5) use beam search for potentially better
# quality but slower processing. For real-time applications, beam_size=1 is
# recommended for optimal performance.
BEAM_SIZE = 1 # default 5
