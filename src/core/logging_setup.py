import logging
import logging.handlers
import os

def setup_logging():
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, "app.log")

    # Get the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Check if handlers are already configured to avoid duplicates
    if logger.hasHandlers():
        return

    # Create console handler
    c_handler = logging.StreamHandler()
    c_handler.setLevel(logging.INFO)

    # Create file handler (Rotating: 10MB, keep 5 backups)
    f_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    f_handler.setLevel(logging.INFO)

    # Create formatter
    log_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    c_handler.setFormatter(log_format)
    f_handler.setFormatter(log_format)

    # Add handlers to the logger
    logger.addHandler(c_handler)
    logger.addHandler(f_handler)

    # Add UI Stream Handler
    from src.services.log_stream import log_stream
    logger.addHandler(log_stream)
