"""Application startup configuration."""

import os
from dependency_injector.wiring import Provide

from app.configs.containers import container


def configure_application():
    """Configure the application and wire the DI container."""
    
    # Configure container based on environment
    container.config.from_dict({
        "logging": {
            "level": os.getenv("LOG_LEVEL", "INFO"),
            "debug_enabled": bool(os.getenv("DEBUG")),
        }
    })
    
    # Wire the container to modules that use dependency injection
    container.wire(modules=[
        "app.tasks.bot_exit_tasks.send_webhook",
        "app.tasks.bot_exit_tasks.aggregate_transcription",
        "app.tasks.bot_exit_tasks",
        "app.configs.dependencies",
    ])
    
    return container


def configure_for_testing():
    """Configure container for testing environment."""
    # Configure container based on environment
    container.config.from_dict({
        "logging": {
            "level": os.getenv("LOG_LEVEL", "INFO"),
            "debug_enabled": bool(os.getenv("DEBUG")),
        }
    })
    
    # Wire the container to modules that use dependency injection
    container.wire(modules=[
        "app.tasks.bot_exit_tasks.send_webhook",
        "app.tasks.bot_exit_tasks.aggregate_transcription",
        "app.tasks.bot_exit_tasks",
        "app.configs.dependencies",
    ])
    
    return container


def shutdown_application():
    """Clean up application resources."""
    global _initialized
    container.unwire()
    _initialized = False


# Global initialization flag
_initialized = False


def initialize_application(testing: bool = False):
    """Initialize the application once.
    
    Args:
        testing: Whether to configure for testing
    """
    global _initialized
    
    if _initialized:
        return container
        
    if testing:
        configure_for_testing()
    else:
        configure_application()
        
    _initialized = True
    return container


def validate_dependency_injection():
    """
    Validate that dependency injection system is working correctly.
    
    This performs generic validation without knowing about specific modules or providers:
    1. Verifies that modules were wired to the container
    2. Tests that the DI mechanism can resolve dependencies by attempting to call
       a DI-enabled function and verifying it doesn't fail with a 'Provide' error
    
    Raises:
        RuntimeError: If dependency injection system is not working
    """
    import inspect
    import asyncio
    from dependency_injector.wiring import Provide
    
    # Check 1: Verify modules are wired
    wired_modules = getattr(container, 'wired_to_modules', [])
    
    if not wired_modules:
        raise RuntimeError("No modules appear to be wired to the dependency injection container")
    
    # Check 2: Find any function with DI and test that DI resolution works
    test_functions = []
    
    for module in wired_modules:
        for attr_name in dir(module):
            if attr_name.startswith('_'):
                continue
                
            attr = getattr(module, attr_name)
            
            if not callable(attr):
                continue
                
            try:
                sig = inspect.signature(attr)
            except (ValueError, TypeError):
                continue
            
            # Look for functions that have Provide objects (DI-enabled functions)
            has_di = any(
                isinstance(param.default, Provide) 
                for param in sig.parameters.values() 
                if param.default is not inspect.Parameter.empty
            )
            
            if has_di:
                test_functions.append((module.__name__, attr_name, attr))
                break  # Only need one function per module for testing
    
    if not test_functions:
        raise RuntimeError("No dependency injection enabled functions found in wired modules")
    
    # Check 3: Test that DI actually resolves dependencies
    for module_name, func_name, func in test_functions:
        try:
            # Create minimal mock arguments for the function
            sig = inspect.signature(func)
            mock_args = []
            
            for param in sig.parameters.values():
                if param.default is inspect.Parameter.empty:
                    # Required parameter - provide None as mock
                    mock_args.append(None)
            
            # Try to call the function - this should NOT fail with 'Provide' error
            # If DI is working, it should fail with a different error (like AttributeError on None)
            if asyncio.iscoroutinefunction(func):
                try:
                    asyncio.run(func(*mock_args))
                except AttributeError as e:
                    if "'Provide' object has no attribute" in str(e):
                        raise RuntimeError(
                            f"Dependency injection not working in {module_name}.{func_name}: "
                            f"Provide objects are not being resolved. {e}"
                        )
                    # Any other AttributeError means DI worked (we got to actual execution)
                except Exception:
                    # Any other exception means DI worked (we got to actual execution)
                    pass
            else:
                try:
                    func(*mock_args)
                except AttributeError as e:
                    if "'Provide' object has no attribute" in str(e):
                        raise RuntimeError(
                            f"Dependency injection not working in {module_name}.{func_name}: "
                            f"Provide objects are not being resolved. {e}"
                        )
                    # Any other AttributeError means DI worked
                except Exception:
                    # Any other exception means DI worked
                    pass
                    
        except RuntimeError:
            raise  # Re-raise our DI validation errors
        except Exception:
            # Ignore other exceptions - they mean DI is working
            pass


def get_container():
    """Get the configured container instance."""
    if not _initialized:
        initialize_application()
    return container