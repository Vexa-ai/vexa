"""Unit tests for TranscriptionFilter in filters.py.

Tests pure filtering logic without any running services.
"""
import pytest
from filters import TranscriptionFilter, BASE_NON_INFORMATIVE_PATTERNS


@pytest.fixture
def filt():
    """Create a fresh TranscriptionFilter for each test."""
    f = TranscriptionFilter()
    # Reset to known state (avoid side effects from filter_config.py)
    f.custom_filters = []
    f.patterns = list(BASE_NON_INFORMATIVE_PATTERNS)
    f.min_character_length = 3
    f.min_real_words = 1
    f.stopwords = {"en": ["the", "and", "for", "you"]}
    f.processed_segments_cache_by_meeting = {}
    return f


# --- Basic acceptance ---

class TestBasicFiltering:
    def test_normal_sentence_passes(self, filt):
        assert filt.filter_segment("Hello world, this is a test", 0.0, 5.0, 1) is True

    def test_blank_audio_filtered(self, filt):
        assert filt.filter_segment("[BLANK_AUDIO]", 0.0, 1.0, 1) is False

    def test_no_audio_filtered(self, filt):
        assert filt.filter_segment("<no audio>", 0.0, 1.0, 1) is False

    def test_inaudible_filtered(self, filt):
        assert filt.filter_segment("<inaudible>", 0.0, 1.0, 1) is False

    def test_empty_string_filtered(self, filt):
        assert filt.filter_segment("", 0.0, 1.0, 1) is False

    def test_whitespace_only_filtered(self, filt):
        assert filt.filter_segment("   ", 0.0, 1.0, 1) is False


# --- Minimum length ---

class TestMinimumLength:
    def test_short_text_filtered(self, filt):
        assert filt.filter_segment("ab", 0.0, 1.0, 1) is False

    def test_exactly_min_length_passes_if_has_real_word(self, filt):
        # "abc" is 3 chars, 1 real word (len >= 3)
        assert filt.filter_segment("abc", 0.0, 1.0, 1) is True

    def test_two_char_with_whitespace_filtered(self, filt):
        assert filt.filter_segment("  hi  ", 0.0, 1.0, 1) is False


# --- Real words counting ---

class TestRealWords:
    def test_only_stopwords_filtered(self, filt):
        """All words are stopwords -> no real words -> filtered."""
        assert filt.filter_segment("the and for you", 0.0, 5.0, 1) is False

    def test_short_words_filtered(self, filt):
        """Words shorter than 3 chars don't count as real words."""
        assert filt.filter_segment("a b c d e", 0.0, 5.0, 1) is False

    def test_bracket_words_excluded(self, filt):
        """Words starting with < or [ don't count as real words.
        'real' has len >= 3 and is not a stopword, so it counts as a real word.
        With min_real_words=1, this passes the filter."""
        assert filt.filter_segment("<tag> [marker] real", 0.0, 5.0, 1) is True

    def test_only_bracket_words_filtered(self, filt):
        """When ALL words start with < or [, no real words remain."""
        assert filt.filter_segment("<tag> [marker] <other>", 0.0, 5.0, 1) is False

    def test_one_real_word_passes(self, filt):
        assert filt.filter_segment("hello", 0.0, 1.0, 1) is True


# --- Stopwords ---

class TestStopwords:
    def test_is_stop_word_english(self, filt):
        assert filt.is_stop_word("the", "en") is True

    def test_is_not_stop_word(self, filt):
        assert filt.is_stop_word("python", "en") is False

    def test_unknown_language_not_stopword(self, filt):
        assert filt.is_stop_word("the", "xx") is False

    def test_stopword_case_insensitive(self, filt):
        assert filt.is_stop_word("THE", "en") is True


# --- Deduplication (time-based) ---

class TestDeduplication:
    def test_identical_text_same_time_filtered(self, filt):
        """Second identical segment at same time is filtered as duplicate."""
        assert filt.filter_segment("hello world", 0.0, 5.0, 1) is True
        assert filt.filter_segment("hello world", 0.0, 5.0, 1) is False

    def test_identical_text_sub_segment_filtered(self, filt):
        """A sub-segment (shorter time window) with same text is filtered."""
        assert filt.filter_segment("hello world", 0.0, 10.0, 1) is True
        assert filt.filter_segment("hello world", 2.0, 8.0, 1) is False

    def test_identical_text_expansion_replaces(self, filt):
        """A wider time window with same text replaces the cached shorter one."""
        assert filt.filter_segment("hello world", 2.0, 8.0, 1) is True
        assert filt.filter_segment("hello world", 0.0, 10.0, 1) is True
        # The old (2.0-8.0) should be removed from cache, new (0.0-10.0) kept
        assert len(filt.processed_segments_cache_by_meeting[1]) == 1
        assert filt.processed_segments_cache_by_meeting[1][0]["start"] == 0.0

    def test_different_meeting_ids_independent(self, filt):
        """Segments with different meeting IDs don't interfere."""
        assert filt.filter_segment("hello world", 0.0, 5.0, 1) is True
        assert filt.filter_segment("hello world", 0.0, 5.0, 2) is True

    def test_different_text_overlapping_shorter_filtered(self, filt):
        """Shorter text fully inside longer cached segment is filtered."""
        assert filt.filter_segment("hello world this is longer text", 0.0, 10.0, 1) is True
        assert filt.filter_segment("hello short", 2.0, 8.0, 1) is False


# --- Cache management ---

class TestCacheManagement:
    def test_clear_cache(self, filt):
        filt.filter_segment("hello world", 0.0, 5.0, 1)
        assert 1 in filt.processed_segments_cache_by_meeting
        filt.clear_processed_segments_cache(1)
        assert 1 not in filt.processed_segments_cache_by_meeting

    def test_clear_nonexistent_cache_no_error(self, filt):
        filt.clear_processed_segments_cache(999)  # Should not raise


# --- Custom filters ---

class TestCustomFilters:
    def test_custom_filter_rejects(self, filt):
        filt.add_custom_filter(lambda text: False)
        assert filt.filter_segment("perfectly good text", 0.0, 5.0, 1) is False

    def test_custom_filter_accepts(self, filt):
        filt.add_custom_filter(lambda text: True)
        assert filt.filter_segment("perfectly good text", 0.0, 5.0, 1) is True

    def test_custom_filter_error_handled(self, filt):
        """Error in custom filter is logged but segment passes (error doesn't reject)."""
        def bad_filter(text):
            raise ValueError("oops")
        bad_filter.__name__ = "bad_filter"
        filt.add_custom_filter(bad_filter)
        # The code catches the exception and continues -- segment is NOT rejected
        assert filt.filter_segment("perfectly good text", 0.0, 5.0, 1) is True


# --- Pattern matching ---

class TestPatterns:
    def test_angle_brackets_filtered(self, filt):
        assert filt.filter_segment("<>", 0.0, 1.0, 1) is False

    def test_heart_emoji_artifact_filtered(self, filt):
        assert filt.filter_segment("<3", 0.0, 1.0, 1) is False

    def test_chevrons_filtered(self, filt):
        assert filt.filter_segment(">>>", 0.0, 1.0, 1) is False
        assert filt.filter_segment("<<<", 0.0, 1.0, 1) is False

    def test_double_chevrons_filtered(self, filt):
        assert filt.filter_segment(">>", 0.0, 1.0, 1) is False
        assert filt.filter_segment("<<", 0.0, 1.0, 1) is False
