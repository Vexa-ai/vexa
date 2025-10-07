"""Enhanced logger implementation combining StandardLogger and DebugLogger features."""

import logging
from logging import Logger
import os
import sys

from app.core.protocols.logger_protocol import LoggerProtocol
from .formatters import ColorLognameFormatter, DebugFormatter


class StandardLogger(LoggerProtocol):
    """
    Enhanced logger implementation that combines production and debug features.
    
    Features:
    - LOG_LEVEL environment variable support (INFO, DEBUG, ERROR, etc.)
    - DEBUG namespace filtering (e.g., DEBUG=bot:* for npm debug-like behavior)
    - Structured logging format with timestamps
    - File and line number information
    - Dual formatter support (standard vs debug style)
    """
    
    def __init__(self, name: str, level: int = logging.INFO):
        """Initialize standard logger.
        
        Args:
            name: Logger name/namespace (e.g., "bot:webhook", "api-gateway")
            level: Default logging level (overridden by LOG_LEVEL env var)
        """
        self.name = name
        self._debug_enabled = self._is_debug_enabled()
        self._logger = self._create_logger(level)
        
    def _is_debug_enabled(self) -> bool:
        """Check if this namespace should be enabled based on DEBUG env var."""
        debug_env = os.getenv('DEBUG', '').strip()
        if not debug_env:
            return False
        
        # Parse comma-separated namespaces
        enabled_namespaces = [ns.strip() for ns in debug_env.split(',') if ns.strip()]
        
        for enabled_ns in enabled_namespaces:
            if enabled_ns == '*':
                return True
            elif enabled_ns == self.name:
                return True
            elif enabled_ns.endswith('*'):
                # Wildcard matching (e.g., "bot:*" matches "bot:webhook")
                prefix = enabled_ns[:-1]
                if self.name.startswith(prefix):
                    return True
        
        return False
        
    def _create_logger(self, level: int) -> logging.Logger:
        """Create and configure the underlying logger."""
        logger = logging.getLogger(self.name)
        # logger = Logger.manager.getLogger(self.name)
        
        # Check for LOG_LEVEL override
        custom_level = os.environ.get('LOG_LEVEL')
        if custom_level:
            try:
                numeric_level = getattr(logging, custom_level.upper(), None)
                if numeric_level is not None:
                    level = numeric_level
            except AttributeError:
                pass

        logger.setLevel(level)
        
        # Only configure if not already configured
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            
            # # Choose formatter based on DEBUG environment variable
            # if self._debug_enabled:
            #     formatter = DebugFormatter(self.name)
            # else:
            format_template = '{asctime} [{levelname:^8s}] {name} {message} ({filename}:{lineno:d}:{funcName})'
            formatter = ColorLognameFormatter(self.name, format_template, datefmt='%Y-%m-%d %H:%M:%S', style='{')
            
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.propagate = False
            
        return logger
    
    def _log_with_caller_info(self, level: int, message: str, *args, **kwargs):
        """Log message while preserving the caller's file/line information."""
        # Check if this level should be logged based on LOG_LEVEL
        if not self._logger.isEnabledFor(level):
            return
            
        # Additional DEBUG namespace filtering for debug messages
        if not self._debug_enabled:
            return
            
        # print(f"_log_with_caller_info {self.name} - {level} : {self._debug_enabled} handlers {len(self._logger.handlers)}")
        # Get caller's frame (skip this method and the public method)
        frame = sys._getframe(2)
        
        # Create a custom LogRecord with caller's info
        record = self._logger.makeRecord(
            name=self.name,
            level=level,
            fn=frame.f_code.co_filename,
            lno=frame.f_lineno,
            msg=message,
            args=args,
            exc_info=kwargs.get('exc_info'),
            func=frame.f_code.co_name,
            extra=kwargs.get('extra'),
            sinfo=kwargs.get('stack_info')
        )
        
        self._logger.handle(record)
    
    def debug(self, message: str, *args, **kwargs) -> None:
        """Log a debug message with caller's file/line info."""
        self._log_with_caller_info(logging.DEBUG, message, *args, **kwargs)
    
    def info(self, message: str, *args, **kwargs) -> None:
        """Log an info message with caller's file/line info."""
        self._log_with_caller_info(logging.INFO, message, *args, **kwargs)
    
    def warning(self, message: str, *args, **kwargs) -> None:
        """Log a warning message with caller's file/line info."""
        self._log_with_caller_info(logging.WARNING, message, *args, **kwargs)
    
    def error(self, message: str, *args, **kwargs) -> None:
        """Log an error message with caller's file/line info."""
        self._log_with_caller_info(logging.ERROR, message, *args, **kwargs)
    
    def exception(self, message: str, *args, **kwargs) -> None:
        """Log an exception with traceback and caller's file/line info."""
        # For exception, we need to ensure exc_info is captured
        kwargs.setdefault('exc_info', True)
        self._log_with_caller_info(logging.ERROR, message, *args, **kwargs)
    
    def critical(self, message: str, *args, **kwargs) -> None:
        """Log a critical message with caller's file/line info."""
        self._log_with_caller_info(logging.CRITICAL, message, *args, **kwargs)