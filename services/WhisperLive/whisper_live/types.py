"""
Data types for WhisperLive remote transcription.

Extracted from the original transcriber.py / faster-whisper to avoid
depending on the full faster-whisper package in remote-only mode.
"""

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple, Union


@dataclass
class Word:
    start: float
    end: float
    word: str
    probability: float


@dataclass
class Segment:
    id: int
    seek: int
    start: float
    end: float
    text: str
    tokens: List[int]
    avg_logprob: float
    compression_ratio: float
    no_speech_prob: float
    words: Optional[List[Word]]
    temperature: Optional[float] = None


@dataclass
class VadOptions:
    onset: float = 0.5
    offset: Optional[float] = None
    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    max_speech_duration_s: float = float("inf")
    min_silence_duration_ms: int = 2000
    speech_pad_ms: int = 400


@dataclass
class TranscriptionOptions:
    beam_size: int = 5
    best_of: int = 5
    patience: float = 1.0
    length_penalty: float = 1.0
    repetition_penalty: float = 1.0
    no_repeat_ngram_size: int = 0
    log_prob_threshold: Optional[float] = -1.0
    no_speech_threshold: Optional[float] = 0.6
    compression_ratio_threshold: Optional[float] = 2.4
    condition_on_previous_text: bool = True
    prompt_reset_on_temperature: float = 0.5
    temperatures: List[float] = field(default_factory=lambda: [0.0])
    initial_prompt: Optional[Union[str, Iterable[int]]] = None
    prefix: Optional[str] = None
    suppress_blank: bool = True
    suppress_tokens: Optional[List[int]] = field(default_factory=lambda: [-1])
    without_timestamps: bool = False
    max_initial_timestamp: float = 1.0
    word_timestamps: bool = False
    prepend_punctuations: str = "\"'([{-"
    append_punctuations: str = "\"'.,:!?)]}"
    multilingual: bool = False
    max_new_tokens: Optional[int] = None
    clip_timestamps: Union[str, List[float]] = "0"
    hallucination_silence_threshold: Optional[float] = None
    hotwords: Optional[str] = None


@dataclass
class TranscriptionInfo:
    language: str
    language_probability: float
    duration: float
    duration_after_vad: float
    all_language_probs: Optional[List[Tuple[str, float]]]
    transcription_options: TranscriptionOptions
    vad_options: VadOptions
