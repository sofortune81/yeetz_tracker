# logger.py
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# Define the log directory relative to this file (project root/logs)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, 'logs')
print(f"DEBUG: Attempting to create logs at {LOG_DIR}") # Add this to debug
os.makedirs(LOG_DIR, exist_ok=True)

def setup_logger(name=None, log_filename="app.log", level=logging.INFO):
    """
    Sets up a logger with a rotating file handler and a console handler.

    Args:
        name (str): The name of the logger (usually __name__).
                    If None, returns the root logger.
        log_filename (str): The name of the file to log to (e.g., 'worker.log').
        level (int): Logging level.
    """
    # 1. Get the logger
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 2. Check if handlers already exist to avoid duplicate logs
    if logger.hasHandlers():
        return logger

    # 3. Prevent propagation to root if this is a named logger
    # (Optional: depends if you want Streamlit/System to catch these too)
    if name:
        logger.propagate = False

    # 4. Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)-12s - %(levelname)-8s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 5. Rotating File Handler (5MB size, keep 3 backups)
    file_path = os.path.join(LOG_DIR, log_filename)
    file_handler = RotatingFileHandler(
        file_path,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # 6. Console Handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # 7. Add Handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # 8. Suppress noisy third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("supabase").setLevel(logging.WARNING)
    logging.getLogger("schwab").setLevel(logging.WARNING)  # Schwab can be verbose

    return logger


def get_logger(module_name):
    """
    Helper for library files to just get a logger without configuring handlers.
    """
    return logging.getLogger(module_name)