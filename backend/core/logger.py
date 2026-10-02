import logging
import sys

def get_logger(name: str):
    """
    Configures and returns a custom logger with a professional format.
    Outputs to the standard console (stdout).
    """
    logger = logging.getLogger(name)
    
    # Avoid adding multiple handlers if the logger already exists
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Define the format: [Date Time] [Level] [Logger Name] - Message
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        # Output to console
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        
        logger.addHandler(console_handler)
        
    return logger