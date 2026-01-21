import logging
import asyncio
from typing import Callable, List
from collections import deque
from nicegui import ui, app

class LogStream(logging.Handler):
    def __init__(self):
        super().__init__()
        self.listeners: List[Callable[[str], None]] = []
        # Keep a small buffer for late subscribers or history
        self.buffer = deque(maxlen=100)
        self.formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')

    def emit(self, record):
        try:
            msg = self.format(record)
            self.buffer.append(msg)

            # Dispatch to listeners safely
            for listener in self.listeners:
                # If listener is a UI element update, it must be thread-safe.
                # We assume listener is a simple function that might need wrapping.
                # However, since we don't know the context, we rely on the listener to handle it
                # OR we try to invoke it in the loop.
                try:
                    listener(msg)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)

    def register(self, listener: Callable[[str], None]):
        self.listeners.append(listener)

    def unregister(self, listener: Callable[[str], None]):
        if listener in self.listeners:
            self.listeners.remove(listener)

log_stream = LogStream()

# Setup
def setup_log_stream():
    root_logger = logging.getLogger()
    root_logger.addHandler(log_stream)
    # Ensure src logger is at least INFO
    logging.getLogger("src").setLevel(logging.INFO)
