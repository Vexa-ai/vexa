"""Custom logging formatters."""

import logging
import os
import sys
from typing import List


class ColorCodes:
    white = "\x1b[38;21m"
    grey = '\033[90m'
    magenta = "\033[35m"
    purple = "\x1b[38;5;21m"
    purple_bold = "\x1b[1;35m"
    light_blue = "\x1b[1;36m"
    cyan = '\033[36m'
    blue = "\x1b[38;5;39m"
    blue_light = "\033[94m"
    green='\033[0;32m' # or '\033[32m' or '\033[92m'
    green_bold = "\x1b[1;32m"
    yellow = '\033[93m' # or '\033[33m'
    yellow_flashy = "\x1b[38;5;226m"
    yellow_bold='\033[1;33m'
    yellow_underlined = "\x1b[33;21m"
    orange='\033[0;34m' # "\033[34m"
    orange_bold = "\x1b[1;34m"
    red_underlined = "\x1b[31;21m"
    red = '\033[31m' # = "\x1b[38;5;196m" ou '\033[0;31m' ou '\033[91m'
    red_bold = "\x1b[31;1m"
    reset = "\x1b[0m" # ou '\033[0m'

# Color codes for different namespaces
COLORS: List[str] = [
    ColorCodes.cyan,
    ColorCodes.purple,
    ColorCodes.magenta,
    ColorCodes.yellow_flashy,
    ColorCodes.orange,
]

class DebugFormatter(logging.Formatter):
    """Custom formatter that mimics npm debug output style."""
    
    def __init__(self, namespace: str):
        """Initialize formatter for the given namespace.
        
        Args:
            namespace: Logger namespace for color assignment
        """
        super().__init__()
        self.namespace = namespace
        # Assign a consistent color based on namespace hash
        self.color = COLORS[hash(namespace) % len(COLORS)]
        
    def format(self, record: logging.LogRecord) -> str:
        """Format log message in npm debug style.
        
        Format: namespace message (file:line:function)
        
        Args:
            record: Log record to format
            
        Returns:
            Formatted log message
        """
        # Get relative filename for cleaner output
        filename = os.path.relpath(record.pathname)
        
        # Format message with args if provided
        if record.args:
            try:
                message = record.msg % record.args
            except (TypeError, ValueError):
                # Fallback for malformed format strings
                message = str(record.msg) + ' ' + str(record.args)
        else:
            message = str(record.msg)
        
        # Add exception info if present
        if record.exc_info:
            message += '\n' + self.formatException(record.exc_info)
        
        # Build the debug-style output
        location_info = f"({filename}:{record.lineno}:{record.funcName})"
        
        if self._should_use_color():
            # Color output
            return f"{self.color}{self.namespace}{ColorCodes.reset} {message} {self.color}{location_info}{ColorCodes.reset}"
        else:
            # No color output
            return f"{self.namespace} {message} {location_info}"
    
    def _should_use_color(self) -> bool:
        """Determine if colored output should be used.
        
        Returns:
            True if colors should be used, False otherwise
        """
        # Don't use colors if NO_COLOR env var is set or output is not a TTY
        return not os.getenv('NO_COLOR') and sys.stdout.isatty()


class StructuredFormatter(logging.Formatter):
    """Structured logging formatter for production environments."""
    
    def __init__(self, include_extra: bool = True):
        """Initialize structured formatter.
        
        Args:
            include_extra: Whether to include extra fields from log records
        """
        super().__init__()
        self.include_extra = include_extra
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log message as structured data.
        
        Args:
            record: Log record to format
            
        Returns:
            Structured log message
        """
        # Base structured data
        log_data = {
            'timestamp': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
            'pathname': record.pathname
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields if enabled and present
        if self.include_extra and hasattr(record, '__dict__'):
            # Include any extra fields added to the log record
            extra_fields = {
                key: value for key, value in record.__dict__.items()
                if key not in log_data and not key.startswith('_')
            }
            if extra_fields:
                log_data['extra'] = extra_fields
        
        # Format as key=value pairs for easy parsing
        formatted_parts = []
        for key, value in log_data.items():
            if isinstance(value, str) and ' ' in value:
                formatted_parts.append(f'{key}="{value}"')
            else:
                formatted_parts.append(f'{key}={value}')
        
        return ' '.join(formatted_parts)



class ColorLognameFormatter(logging.Formatter):
    LEVEL_MAX_LENGTH = 8

    # Format into a dict
    _color_levelname = {
        'DEBUG': f"{ColorCodes.grey}DEBUG{ColorCodes.reset}".center(LEVEL_MAX_LENGTH + len(ColorCodes.reset) + len(ColorCodes.grey), ' '),
        'INFO': f"{ColorCodes.green}{'INFO':^{LEVEL_MAX_LENGTH}s}{ColorCodes.reset}",
        'WARNING': f"{ColorCodes.yellow}WARNING{ColorCodes.reset}".center(LEVEL_MAX_LENGTH + len(ColorCodes.reset) + len(ColorCodes.yellow), ' '),
        'ERROR': f"{ColorCodes.red}ERROR{ColorCodes.reset}".center(LEVEL_MAX_LENGTH + len(ColorCodes.reset) + len(ColorCodes.red), ' '),
        'CRITICAL': f"{ColorCodes.red_bold}{'CRITICAL':^{LEVEL_MAX_LENGTH}s}"
    }

    def __init__(self, namespace: str, fmt='%(levelname)s | %(message)s', *args, **kwargs):
        super().__init__(fmt, *args, **kwargs)
        self.namespace = namespace
        # Assign a consistent color based on namespace hash
        # import hashlib
        # color_index = int(hashlib.sha1(namespace.encode()).hexdigest(), 16) % len(COLORS)
        color_index = hash(namespace) % len(COLORS)
        self.color = COLORS[color_index]

    def _should_use_color(self) -> bool:
        """Determine if colored output should be used.
        
        Returns:
            True if colors should be used, False otherwise
        """
        # Don't use colors if NO_COLOR env var is set or output is not a TTY
        return not os.getenv('NO_COLOR') and sys.stdout.isatty()

    def format(self, record:logging.LogRecord) -> str:
        """Format log message in npm debug style.
        
        Format: time [level] namespace - message (file:line:function)
        
        Args:
            record: Log record to format
            
        Returns:
            Formatted log message
        """

        # Get relative filename for cleaner output
        # record.filename = os.path.relpath(record.pathname)
        
        # Format message with args if provided
        if record.args:
            try:
                message = record.msg % record.args
            except (TypeError, ValueError):
                # Fallback for malformed format strings
                message = str(record.msg) + ' ' + str(record.args)
        else:
            message = str(record.msg)
        
        # Add exception info if present
        if record.exc_info:
            message += '\n' + self.formatException(record.exc_info)
        
        # Build the debug-style output
        # location_info = f"({filename}:{record.lineno}:{record.funcName})"

        record.message = message
        
        if self._should_use_color():
            # When calling format, replace the levelname with a colored version
            # Note: the string size is greatly increased because of the color codes
            record.levelname = self._color_levelname[record.levelname]
            record.name = f"{self.color}{record.name}{ColorCodes.reset}"
            record.filename = f"{self.color}{record.filename}{ColorCodes.reset}"
            record.funcName = f"{self.color}{record.funcName}{ColorCodes.reset}"
            # record.asctime = f"{self.color}{record.asctime}{ColorCodes.reset}"
            # record.lineno = f"{self.color}{record.lineno}{ColorCodes.reset}"

        return super().format(record)
    
