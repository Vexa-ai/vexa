"""Dependency injection container configuration."""

import logging
import os
from dependency_injector import containers, providers

from app.core.protocols.logger_protocol import LoggerProtocol
from app.adapters.logging.debug_logger import DebugLogger
from app.adapters.logging.standard_logger import StandardLogger


class LoggingContainer(containers.DeclarativeContainer):
    """Container for logging dependencies."""
    
    # Configuration provider
    config = providers.Configuration()
    
    # Logger providers
    debug_logger_factory = providers.Factory(
        DebugLogger
        # namespace will be provided at injection time
    )
    
    standard_logger_factory = providers.Factory(
        StandardLogger,
        level=providers.Callable(
            lambda: getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO)
        )
        # name will be provided at injection time
    )
    
    # Specific logger instances for different namespaces
    # webhook_logger = providers.Factory(
    #     DebugLogger,
    #     namespace="bot:webhook",
    # )

    webhook_logger = providers.Factory(
        StandardLogger,
        name="bot:webhook",
        level=providers.Callable(
            lambda: getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO)
        )
    )
    
    # transcription_logger = providers.Factory(
    #     DebugLogger,
    #     namespace="bot:transcription", 
    # )

    transcription_logger = providers.Factory(
        StandardLogger,
        name="bot:transcription", 
        level=providers.Callable(
            lambda: getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO)
        )
    )

    bot_manager_logger = providers.Factory(
        StandardLogger,
        name="bot:manager", 
        level=providers.Callable(
            lambda: getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO)
        )
    )
    
    # Default logger provider - switches based on environment
    default_logger_factory = providers.Selector(
        providers.Callable(lambda: "debug" if os.getenv('DEBUG') else "standard"),
        debug=debug_logger_factory,
        standard=standard_logger_factory
    )


class ApplicationContainer(containers.DeclarativeContainer):
    """Main application dependency injection container."""
    
    # Configuration
    config = providers.Configuration()
    
    # Include logging container
    logging = providers.DependenciesContainer()
    logging.override(LoggingContainer())
    
    # Service providers will be added here
    # Example:
    # webhook_service = providers.Factory(
    #     WebhookService,
    #     logger=logging.default_logger_factory.provided.call("bot:webhook")
    # )


# Global container instance
container = ApplicationContainer()


def get_logger(namespace: str, logger_type: str = "auto") -> LoggerProtocol:
    """Get a logger from the DI container.
    
    Args:
        namespace: Logger namespace (e.g., "bot:webhook")
        logger_type: Type of logger ("auto", "debug", "standard")
        
    Returns:
        Logger instance from the container
    """
    if logger_type == "auto":
        if os.getenv('DEBUG'):
            return container.logging.debug_logger_factory(namespace)
        else:
            return container.logging.standard_logger_factory(namespace)
    elif logger_type == "debug":
        return container.logging.debug_logger_factory(namespace)
    elif logger_type == "standard":
        return container.logging.standard_logger_factory(namespace)
    else:
        raise ValueError(f"Unknown logger type: {logger_type}")


