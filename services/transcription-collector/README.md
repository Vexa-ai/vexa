# Transcription Collector

The Transcription Collector is a service that aggregates and deduplicates transcription segments from multiple WhisperLive servers, storing only meaningful, informative content in PostgreSQL.

## Architecture

- **WebSocket Server**: Accepts connections from WhisperLive servers
- **Redis**: Temporary storage and deduplication
- **PostgreSQL**: Permanent storage for completed segments
- **Filtering System**: Removes non-informative segments

## Filtering System

The Transcription Collector uses a modular filtering system to identify and remove non-informative segments before storing them in the database.

### How Filtering Works

1. Each segment passes through multiple filters:

   - Minimum character length check
   - Pattern matching against known non-informative patterns
   - Real word counting (excluding stopwords and special symbols)
   - Custom filter functions

2. Segments are only stored in PostgreSQL if they pass all filters

### Customizing Filters

You can easily customize the filtering behavior by editing the `filter_config.py` file:

```python
# Add your own patterns to filter out
ADDITIONAL_FILTER_PATTERNS = [
    r"^testing$",  # Filter out segments that are just "testing"
    # Add more patterns here
]

# Set minimum thresholds
MIN_CHARACTER_LENGTH = 3
MIN_REAL_WORDS = 1

# Define custom filter functions
def filter_out_repeated_characters(text):
    """Filter out strings with excessive character repetition"""
    import re
    if re.search(r'(.)\1{4,}', text):
        return False
    return True

# Register your custom filters
CUSTOM_FILTERS = [
    filter_out_repeated_characters,
    # Add more custom filter functions here
]

# Add language-specific stopwords
STOPWORDS = {
    "en": ["the", "and", "for", "you", "this", "that"],
    #TODO: Add other languages as needed
}
```

### Adding New Filter Functions

To create a new filter:

1. Define your function in `filter_config.py`
2. The function should:
   - Take a text parameter
   - Return `True` to keep the segment or `False` to filter it out
3. Add your function to the `CUSTOM_FILTERS` list

Example:

```python
def filter_out_short_words_only(text):
    """Filter out segments with only short words (1-2 chars)"""
    words = text.split()
    if all(len(word) <= 2 for word in words):
        return False
    return True

CUSTOM_FILTERS.append(filter_out_short_words_only)
```

## API Endpoints

- `GET /health`: Health check endpoint
- `GET /stats`: Statistics about stored transcriptions
- `WebSocket /collector`: WebSocket endpoint for WhisperLive servers

## Deployment

The Transcription Collector is designed to run as a Docker container alongside Redis and PostgreSQL. See the docker-compose.yml file for deployment configuration.
