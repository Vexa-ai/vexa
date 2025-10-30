"""FastAPI dependency injection providers."""

from typing import Annotated
from fastapi import Depends
from dependency_injector.wiring import inject, Provide

from app.core.protocols.logger_protocol import LoggerProtocol
from app.configs.containers import ApplicationContainer


# Logger dependency for FastAPI routes
@inject
def get_webhook_logger(
    logger_factory=Provide[ApplicationContainer.logging.default_logger_factory]
) -> LoggerProtocol:
    """Get logger for webhook operations."""
    return logger_factory("bot:webhook")


@inject
def get_task_logger(
    logger_factory=Provide[ApplicationContainer.logging.default_logger_factory]
) -> LoggerProtocol:
    """Get logger for task operations."""
    return logger_factory("bot:task")


@inject
def get_database_logger(
    logger_factory=Provide[ApplicationContainer.logging.default_logger_factory]
) -> LoggerProtocol:
    """Get logger for database operations."""
    return logger_factory("bot:database")


@inject
def get_docker_logger(
    logger_factory=Provide[ApplicationContainer.logging.default_logger_factory]
) -> LoggerProtocol:
    """Get logger for Docker operations."""
    return logger_factory("bot:docker")


# Type aliases for cleaner dependency injection in routes
WebhookLogger = Annotated[LoggerProtocol, Depends(get_webhook_logger)]
TaskLogger = Annotated[LoggerProtocol, Depends(get_task_logger)]
DatabaseLogger = Annotated[LoggerProtocol, Depends(get_database_logger)]
DockerLogger = Annotated[LoggerProtocol, Depends(get_docker_logger)]


def create_logger_dependency(namespace: str):
    """Create a custom logger dependency for a specific namespace.
    
    Args:
        namespace: Logger namespace
        
    Returns:
        FastAPI dependency function
        
    Example:
        # Create custom logger dependency
        MyLogger = Annotated[LoggerProtocol, Depends(create_logger_dependency("bot:custom"))]
        
        # Use in route
        @app.get("/custom")
        def custom_route(logger: MyLogger):
            logger.info("Custom route called")
    """
    @inject
    def get_custom_logger(
        logger_factory=Provide[ApplicationContainer.logging.default_logger_factory]
    ) -> LoggerProtocol:
        return logger_factory(namespace)
    
    return get_custom_logger