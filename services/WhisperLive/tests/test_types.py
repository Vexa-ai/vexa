"""Unit tests for WhisperLive types — extracted dataclasses."""
import pytest
from whisper_live.types import VadOptions, Segment, TranscriptionOptions, TranscriptionInfo, Word


class TestVadOptions:
    def test_defaults(self):
        opts = VadOptions()
        assert opts.onset == 0.5
        assert opts.offset is None
        assert opts.threshold == 0.5

    def test_onset_kwarg(self):
        """Regression: server.py passes {"onset": 0.5} as kwargs."""
        opts = VadOptions(onset=0.8)
        assert opts.onset == 0.8

    def test_onset_offset_kwargs(self):
        opts = VadOptions(onset=0.3, offset=0.2)
        assert opts.onset == 0.3
        assert opts.offset == 0.2

    def test_from_dict_spread(self):
        """Regression: remote_transcriber.py does VadOptions(**params)."""
        params = {"onset": 0.5}
        opts = VadOptions(**params)
        assert opts.onset == 0.5

    def test_full_dict_spread(self):
        params = {
            "onset": 0.6,
            "offset": 0.3,
            "threshold": 0.4,
            "min_speech_duration_ms": 500,
            "max_speech_duration_s": 30.0,
            "min_silence_duration_ms": 1000,
            "speech_pad_ms": 200,
        }
        opts = VadOptions(**params)
        assert opts.onset == 0.6
        assert opts.speech_pad_ms == 200


class TestSegment:
    def test_minimal(self):
        seg = Segment(
            id=0, seek=0, start=0.0, end=1.0, text="hello",
            tokens=[], avg_logprob=-0.5, compression_ratio=1.0,
            no_speech_prob=0.0, words=None,
        )
        assert seg.text == "hello"
        assert seg.temperature is None

    def test_with_words(self):
        w = Word(start=0.0, end=0.5, word="hello", probability=0.99)
        seg = Segment(
            id=0, seek=0, start=0.0, end=0.5, text="hello",
            tokens=[1, 2], avg_logprob=-0.3, compression_ratio=1.1,
            no_speech_prob=0.01, words=[w],
        )
        assert len(seg.words) == 1
        assert seg.words[0].word == "hello"


class TestTranscriptionOptions:
    def test_defaults(self):
        opts = TranscriptionOptions()
        assert opts.beam_size == 5
        assert opts.temperatures == [0.0]
        assert opts.word_timestamps is False

    def test_custom(self):
        opts = TranscriptionOptions(beam_size=3, word_timestamps=True)
        assert opts.beam_size == 3
        assert opts.word_timestamps is True


class TestTranscriptionInfo:
    def test_construction(self):
        info = TranscriptionInfo(
            language="en",
            language_probability=0.99,
            duration=5.0,
            duration_after_vad=4.5,
            all_language_probs=None,
            transcription_options=TranscriptionOptions(),
            vad_options=VadOptions(onset=0.5),
        )
        assert info.language == "en"
        assert info.vad_options.onset == 0.5
