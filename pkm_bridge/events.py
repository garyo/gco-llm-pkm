"""Server-Sent Events (SSE) manager for real-time notifications."""

import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Dict, Set, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logger = logging.getLogger(__name__)


class SSEEventManager:
    """Manages Server-Sent Events and broadcasts to connected clients."""

    def __init__(self):
        self.clients: Set[queue.Queue] = set()
        self.lock = threading.Lock()
        self.file_watcher: Optional['FileWatcher'] = None

    def add_client(self) -> queue.Queue:
        """Add a new SSE client and return its message queue."""
        client_queue = queue.Queue(maxsize=100)
        with self.lock:
            self.clients.add(client_queue)
        logger.info(f"SSE client connected. Total clients: {len(self.clients)}")
        return client_queue

    def remove_client(self, client_queue: queue.Queue):
        """Remove an SSE client."""
        with self.lock:
            self.clients.discard(client_queue)
        logger.info(f"SSE client disconnected. Total clients: {len(self.clients)}")

    def broadcast(self, event_type: str, data: Dict):
        """Broadcast an event to all connected clients."""
        message = {
            "type": event_type,
            "data": data,
            "timestamp": int(time.time())
        }

        # Remove disconnected clients
        disconnected = set()
        with self.lock:
            for client_queue in self.clients:
                try:
                    client_queue.put_nowait(message)
                    logger.debug(f"Sent {event_type} to client")
                except queue.Full:
                    logger.warning("Client queue full, dropping message")
                    disconnected.add(client_queue)

            # Clean up disconnected clients
            self.clients -= disconnected

    def start_file_watcher(self, directories: list[Path]):
        """Start watching directories for file changes."""
        if self.file_watcher:
            logger.warning("File watcher already running")
            return

        self.file_watcher = FileWatcher(self, directories)
        self.file_watcher.start()
        logger.info(f"Started file watcher for {len(directories)} directories")

    def stop_file_watcher(self):
        """Stop the file watcher."""
        if self.file_watcher:
            self.file_watcher.stop()
            self.file_watcher = None
            logger.info("Stopped file watcher")


class FileWatcher:
    """Watches file system for changes and emits SSE events."""

    def __init__(self, event_manager: SSEEventManager, directories: list[Path]):
        self.event_manager = event_manager
        self.directories = directories
        self.observer = Observer()
        self.handler = FileChangeHandler(event_manager)

    def start(self):
        """Start watching the directories."""
        for directory in self.directories:
            if directory.exists():
                self.observer.schedule(
                    self.handler,
                    str(directory),
                    recursive=True
                )
                logger.info(f"Watching directory: {directory}")
            else:
                logger.warning(f"Directory does not exist: {directory}")

        self.observer.start()

    def stop(self):
        """Stop watching."""
        self.observer.stop()
        self.observer.join()


class FileChangeHandler(FileSystemEventHandler):
    """Handles file system events and broadcasts them via SSE."""

    def __init__(self, event_manager: SSEEventManager):
        self.event_manager = event_manager
        # Debounce: Track recent events to avoid spamming
        self.recent_events: Dict[str, float] = {}
        self.debounce_seconds = 0.5

    def _should_notify(self, file_path: str) -> bool:
        """Check if we should notify about this file change (debouncing)."""
        now = time.time()
        last_event = self.recent_events.get(file_path, 0)

        if now - last_event < self.debounce_seconds:
            return False

        self.recent_events[file_path] = now
        # Clean up old entries
        self.recent_events = {
            path: timestamp
            for path, timestamp in self.recent_events.items()
            if now - timestamp < 60  # Keep last minute
        }
        return True

    def _is_relevant_file(self, path: str) -> bool:
        """Check if this is a file type we care about."""
        # Only notify about org, md, and txt files
        return path.endswith(('.org', '.md', '.txt'))

    def on_modified(self, event: FileSystemEvent):
        """Handle file modification events."""
        if event.is_directory:
            return

        file_path = str(event.src_path)

        if not self._is_relevant_file(file_path):
            return

        if not self._should_notify(file_path):
            return

        # Get file stats
        try:
            file_stat = Path(file_path).stat()
            mtime = int(file_stat.st_mtime)

            self.event_manager.broadcast('file_changed', {
                'path': file_path,
                'mtime': mtime,
                'size': file_stat.st_size
            })
        except Exception as e:
            logger.error(f"Error getting file stats for {file_path}: {e}")


# Global event manager instance
event_manager = SSEEventManager()
