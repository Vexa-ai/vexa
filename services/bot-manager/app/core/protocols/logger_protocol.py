"""Logger protocol interface for dependency injection."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class LoggerProtocol(Protocol):
    """Protocol defining the logger interface for dependency injection."""
    
    def debug(self, message: str, *args, **kwargs) -> None:
        """Log a debug message.
        
        Args:
            message: The message to log
            *args: Positional arguments for string formatting
            **kwargs: Additional logging parameters
        """
        ...
    
    def info(self, message: str, *args, **kwargs) -> None:
        """Log an info message.
        
        Args:
            message: The message to log
            *args: Positional arguments for string formatting
            **kwargs: Additional logging parameters
        """
        ...
    
    def warning(self, message: str, *args, **kwargs) -> None:
        """Log a warning message.
        
        Args:
            message: The message to log
            *args: Positional arguments for string formatting
            **kwargs: Additional logging parameters
        """
        ...
    
    def error(self, message: str, *args, **kwargs) -> None:
        """Log an error message.
        
        Args:
            message: The message to log
            *args: Positional arguments for string formatting
            **kwargs: Additional logging parameters
        """
        ...
    
    def exception(self, message: str, *args, **kwargs) -> None:
        """Log an exception with traceback.
        
        Args:
            message: The message to log
            *args: Positional arguments for string formatting
            **kwargs: Additional logging parameters
        """
        ...
    
    def critical(self, message: str, *args, **kwargs) -> None:
        """Log a critical message.
        
        Args:
            message: The message to log
            *args: Positional arguments for string formatting
            **kwargs: Additional logging parameters
        """
        ...