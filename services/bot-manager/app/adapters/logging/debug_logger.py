"""Debug logger implementation that mimics npm debug package behavior."""

import logging
import os
import sys
from typing import Optional

from app.core.protocols.logger_protocol import LoggerProtocol
from .formatters import DebugFormatter


class DebugLogger(LoggerProtocol):
    """
    Debug logger implementation that mimics npm debug package behavior.
    
    Features:
    - Namespace-based filtering via DEBUG environment variable
    - Preserves original file/line numbers in logs
    - Supports wildcard patterns (e.g., DEBUG=bot:*)
    - Color-coded output by namespace
    
    Usage:
        DEBUG=bot:* python script.py           # Enable all bot:* namespaces
        DEBUG=bot:webhook python script.py     # Enable only bot:webhook
        DEBUG=* python script.py               # Enable all debug output
    """
    
    def __init__(self, namespace: str):
        """Initialize debug logger for the given namespace.
        
        Args:
            namespace: Logger namespace (e.g., "bot:webhook")
        """
        self.namespace = namespace
        self._logger = self._create_logger()
        self._enabled = self._is_enabled()
        
    def _create_logger(self) -> logging.Logger:
        """Create and configure the underlying logger."""
        logger = logging.getLogger(self.namespace)
        
        # Only configure if not already configured
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            
            # Custom formatter that preserves caller's file/line info
            formatter = DebugFormatter(self.namespace)
            handler.setFormatter(formatter)
            
            logger.addHandler(handler)
            logger.propagate = False
            
        # Set level based on whether debug is enabled
        logger.setLevel(logging.DEBUG if self._is_enabled() else logging.CRITICAL)
        
        return logger
    
    def _is_enabled(self) -> bool:
        """Check if this namespace should be enabled based on DEBUG env var."""
        debug_env = os.getenv('DEBUG', '').strip()
        if not debug_env:
            return False
        
        # Parse comma-separated namespaces
        enabled_namespaces = [ns.strip() for ns in debug_env.split(',') if ns.strip()]
        
        for enabled_ns in enabled_namespaces:
            if enabled_ns == '*':
                return True
            elif enabled_ns == self.namespace:
                return True
            elif enabled_ns.endswith('*'):
                # Wildcard matching (e.g., "bot:*" matches "bot:webhook")
                prefix = enabled_ns[:-1]
                if self.namespace.startswith(prefix):
                    return True
        
        return False
    
    def _log_with_caller_info(self, level: int, message: str, *args, **kwargs):
        """Log message while preserving the caller's file/line information."""
        if not self._enabled and level < logging.ERROR:
            return
            
        # Get caller's frame (skip this method and the public method)
        frame = sys._getframe(2)
        
        # Create a custom LogRecord with caller's info
        record = self._logger.makeRecord(
            name=self.namespace,
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
        kwargs['exc_info'] = True
        self._log_with_caller_info(logging.ERROR, message, *args, **kwargs)
    
    def critical(self, message: str, *args, **kwargs) -> None:
        """Log a critical message with caller's file/line info."""
        self._log_with_caller_info(logging.CRITICAL, message, *args, **kwargs)