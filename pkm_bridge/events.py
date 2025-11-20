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
        self.client_sessions: Dict[queue.Queue, Optional[str]] = {}  # Map client to session_id
        self.lock = threading.Lock()
        self.file_watcher: Optional['FileWatcher'] = None

    def add_client(self, session_id: Optional[str] = None) -> queue.Queue:
        """Add a new SSE client and return its message queue.

        Args:
            session_id: Optional session ID to associate with this client

        Returns:
            Message queue for this client
        """
        client_queue = queue.Queue(maxsize=100)
        with self.lock:
            self.clients.add(client_queue)
            self.client_sessions[client_queue] = session_id
            # Get all active sessions
            active_sessions = [sid for sid in self.client_sessions.values() if sid]
        logger.info(f"SSE client connected (session: {session_id}). Total clients: {len(self.clients)}, Active sessions: {active_sessions}")
        return client_queue

    def remove_client(self, client_queue: queue.Queue):
        """Remove an SSE client."""
        with self.lock:
            session_id = self.client_sessions.get(client_queue)
            self.clients.discard(client_queue)
            self.client_sessions.pop(client_queue, None)
            # Get remaining active sessions
            active_sessions = [sid for sid in self.client_sessions.values() if sid]
        logger.info(f"SSE client disconnected (session: {session_id}). Total clients: {len(self.clients)}, Active sessions: {active_sessions}")

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

    def broadcast_to_session(self, session_id: str, event_type: str, data: Dict):
        """Broadcast an event only to clients in a specific session.

        Args:
            session_id: Session ID to send to
            event_type: Type of event
            data: Event data
        """
        message = {
            "type": event_type,
            "data": data,
            "timestamp": int(time.time())
        }

        # Remove disconnected clients
        disconnected = set()
        sent_count = 0
        with self.lock:
            for client_queue in self.clients:
                # Only send to clients in this session
                if self.client_sessions.get(client_queue) == session_id:
                    try:
                        client_queue.put_nowait(message)
                        logger.debug(f"Sent {event_type} to client in session {session_id}")
                        sent_count += 1
                    except queue.Full:
                        logger.warning("Client queue full, dropping message")
                        disconnected.add(client_queue)

            # Clean up disconnected clients
            self.clients -= disconnected

        logger.info(f"Sent {event_type} to {sent_count} client(s) in session {session_id}")

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
        # Get filename from path
        filename = Path(path).name

        # Ignore dotfiles (hidden files, Syncthing temp files like .!12345!file.md, etc.)
        if filename.startswith('.'):
            return False

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
