# Custom filter configuration file
# This file can be edited to add or modify filtering behavior
# without changing the core code

# Additional patterns to filter out beyond the default ones
ADDITIONAL_FILTER_PATTERNS = [
    # Add your own patterns here
    r"^testing$",       # Example: filter out segments that are just "testing"
    r"^test[\s\d]*$",   # Example: filter out segments like "test", "test 123"
    r"^hello[\s\d]*$",  # Example: filter out segments like "hello", "hello 123"
]

# Minimum character length for a segment to be considered informative
MIN_CHARACTER_LENGTH = 3

# Minimum number of real words (3+ chars) for a segment to be considered informative
MIN_REAL_WORDS = 1

# Define your own custom filter functions here
# Each function should take text as input and return True to keep or False to filter out

def filter_out_repeated_characters(text):
    """Filter out strings with excessive character repetition, like 'aaaaaa' or 'hahahaha'"""
    # If any character appears more than 4 times in a row, filter it out
    import re
    if re.search(r'(.)\1{4,}', text):
        return False
    return True

# List of custom filter functions to apply
CUSTOM_FILTERS = [
    filter_out_repeated_characters,
    # Add your own functions here
]

# Language-specific stopwords to ignore when counting "real words"
STOPWORDS = {
    "en": ["the", "and", "for", "you", "this", "that", "with", "from", "have", "are"],
    # Add other languages as needed
}
