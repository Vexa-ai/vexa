"""
Remote transcriber interface: delegates to AWS Transcribe Streaming via transcriber module.
This module provides an abstraction for "remote" transcription backends (e.g. AWS).
"""
from gateway.transcriber import push_speaker_event, push_to_redis, run_aws_transcribe_session

__all__ = ["run_aws_transcribe_session", "push_to_redis", "push_speaker_event"]
