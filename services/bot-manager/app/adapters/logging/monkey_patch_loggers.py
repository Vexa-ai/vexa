import os
import logging
from app.adapters.logging import StandardLogger

def swapLogger(getLogger, logger_name):
    log_level_str = os.environ.get('LOG_LEVEL', 'INFO')
    try:
        numeric_level = getattr(logging, log_level_str.upper())
    except AttributeError:
        numeric_level = logging.INFO

    logger = getLogger(logger_name)
    # logger.handlers = []
    # logger.propagate = False
    custom_logger = StandardLogger(logger_name, level=numeric_level)
    
    if not hasattr(logger, '_patched'):
        print(f"Monkey patching {logger_name}: debug_enabled={custom_logger._debug_enabled}")
        logger._patched = True
    
    # Monkey patch the logger methods with our custom logger methods
    # This preserves the DEBUG filtering and custom formatting
    logger.debug = custom_logger.debug
    logger.info = custom_logger.info
    logger.warning = custom_logger.warning
    logger.error = custom_logger.error
    logger.exception = custom_logger.exception
    logger.critical = custom_logger.critical

    # Don't add handler - monkey patched methods handle logging through StandardLogger
    # Add our custom logger's handlers
    # # Don't clear handlers or add new ones - let the custom methods handle everything
    # # The filtering happens in the StandardLogger methods, not at the handler level
    # for handler in custom_logger._logger.handlers:
    #     logger.addHandler(handler)
    #     logger.setLevel(custom_logger._logger.level)

    return logger


def monkey_patch_loggers(loggers_to_patch:list[str]=None):
    """Path existing logger in order to have unified logging"""   

    if (loggers_to_patch):
        # Replace handlers for all SQLAlchemy loggers consistently
        for logger_name in loggers_to_patch:
            swapLogger(logging.getLogger, logger_name)
    else : # path all existing loggers
        # Store original getLogger function
        original_getLogger = logging.getLogger
        
        # Create a wrapper that returns StandardLogger instances
        def custom_getLogger(name=None):
            if name is None:
                name = 'root'
            
            # Temporarily restore original getLogger to avoid recursion
            logging.getLogger = original_getLogger
            try:             
                logger = swapLogger(original_getLogger, name)
                return logger
            finally:
                # Restore our custom getLogger
                logging.getLogger = custom_getLogger
        
        # Replace the getLogger function
        logging.getLogger = custom_getLogger
