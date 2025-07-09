"""
Configuration settings for the WhisperLive server.

This file centralizes settings that control the server's audio processing,
transcription, and voice activity detection behavior. Modifying these values
allows for fine-tuning the server's performance and transcription latency.
"""

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
MIN_AUDIO_S = 1.0


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


# Transcription Output Management
# -------------------------------
# These settings control how the transcribed text is managed and sent to the client.

# If the transcription model produces the exact same output this many times in a
# row, it's considered to be "stuck." This is a safeguard to prevent repetitive
# outputs under certain conditions.
SAME_OUTPUT_THRESHOLD = 10

# If there's a pause in speech (i.e., Whisper produces no new text), the server
# will continue to send the previously transcribed segments to the client for this
# duration (in seconds). This creates a more stable output for the user.
SHOW_PREV_OUT_THRESH_S = 5

# If there has been no speech for this duration (in seconds), an empty string is
# added to the transcript. This helps to visually represent a pause in the
# conversation.
ADD_PAUSE_THRESH_S = 3 