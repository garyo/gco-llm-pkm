"""Server-Sent Events (SSE) manager for real-time notifications."""

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Set

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .fileio import content_hash
from .version_store import version_store

logger = logging.getLogger(__name__)


class SSEEventManager:
    """Manages Server-Sent Events and broadcasts to connected clients."""

    def __init__(self):
        self.clients: Set[queue.Queue] = set()
        self.client_sessions: Dict[queue.Queue, Optional[str]] = {}  # Map client to session_id
        self.lock = threading.Lock()
        self.file_watcher: Optional["FileWatcher"] = None

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
        logger.info(
            f"SSE client connected (session: {session_id}). "
            f"Total clients: {len(self.clients)}, Active sessions: {active_sessions}"
        )
        return client_queue

    def remove_client(self, client_queue: queue.Queue):
        """Remove an SSE client."""
        with self.lock:
            session_id = self.client_sessions.get(client_queue)
            self.clients.discard(client_queue)
            self.client_sessions.pop(client_queue, None)
            # Get remaining active sessions
            active_sessions = [sid for sid in self.client_sessions.values() if sid]
        logger.info(
            f"SSE client disconnected (session: {session_id}). "
            f"Total clients: {len(self.clients)}, Active sessions: {active_sessions}"
        )

    def broadcast(self, event_type: str, data: Dict):
        """Broadcast an event to all connected clients."""
        message = {"type": event_type, "data": data, "timestamp": int(time.time())}

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
        message = {"type": event_type, "data": data, "timestamp": int(time.time())}

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

    def start_file_watcher(self, roots: Dict[str, Path]):
        """Start watching the note roots, keyed by their path prefix ('org', 'logseq')."""
        if self.file_watcher:
            logger.warning("File watcher already running")
            return

        self.file_watcher = FileWatcher(self, roots)
        self.file_watcher.start()
        logger.info(f"Started file watcher for {len(roots)} directories")

    def stop_file_watcher(self):
        """Stop the file watcher."""
        if self.file_watcher:
            self.file_watcher.stop()
            self.file_watcher = None
            logger.info("Stopped file watcher")


class FileWatcher:
    """Watches the note roots for changes and emits SSE events."""

    def __init__(self, event_manager: SSEEventManager, roots: Dict[str, Path]):
        self.roots = roots
        self.observer = Observer()
        self.handler = FileChangeHandler(event_manager, roots)

    def start(self):
        """Start watching the directories."""
        for prefix, directory in self.roots.items():
            if directory.exists():
                self.observer.schedule(self.handler, str(directory), recursive=True)
                logger.info(f"Watching directory ({prefix}): {directory}")
            else:
                logger.warning(f"Directory does not exist: {directory}")

        self.observer.start()

    def stop(self):
        """Stop watching."""
        self.observer.stop()
        self.observer.join()
        self.handler.cancel_pending()


class FileChangeHandler(FileSystemEventHandler):
    """Turns raw filesystem events into content-aware `file_changed` / `file_deleted` events.

    Every kind of event (in-place modify, create, the rename that ends an atomic write or a
    Syncthing delivery, delete) is treated the same way: let the path settle briefly, then
    look at what is actually on disk. An event is broadcast only when the content hash
    differs from the last one seen for that path, so bursts collapse and rewrites of
    identical content stay silent.
    """

    SETTLE_SECONDS = 0.3
    WATCHED_SUFFIXES = (".org", ".md", ".txt")

    def __init__(self, event_manager: SSEEventManager, roots: Dict[str, Path]):
        self.event_manager = event_manager
        self.roots = {prefix: root.resolve() for prefix, root in roots.items()}
        self._timers: Dict[str, threading.Timer] = {}
        self._last_hash: Dict[str, str] = {}
        self._lock = threading.Lock()

    def on_modified(self, event: FileSystemEvent):
        self._touch(event.src_path, event.is_directory)

    def on_created(self, event: FileSystemEvent):
        self._touch(event.src_path, event.is_directory)

    def on_deleted(self, event: FileSystemEvent):
        self._touch(event.src_path, event.is_directory)

    def on_moved(self, event: FileSystemEvent):
        self._touch(event.src_path, event.is_directory)
        self._touch(event.dest_path, event.is_directory)

    def cancel_pending(self):
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

    def _touch(self, raw_path, is_directory: bool):
        if is_directory:
            return
        path = os.fsdecode(raw_path)
        if not self._is_relevant_file(path):
            return
        with self._lock:
            if pending := self._timers.pop(path, None):
                pending.cancel()
            timer = threading.Timer(self.SETTLE_SECONDS, self._inspect, args=(path,))
            timer.daemon = True
            self._timers[path] = timer
            timer.start()

    def _is_relevant_file(self, path: str) -> bool:
        # Dotfiles cover our own atomic-write temps, Syncthing's .syncthing.*.tmp,
        # and Emacs lock files.
        name = Path(path).name
        return not name.startswith(".") and name.endswith(self.WATCHED_SUFFIXES)

    def _file_key(self, path: str) -> Optional[str]:
        resolved = Path(path).resolve()
        for prefix, root in self.roots.items():
            if resolved.is_relative_to(root):
                return f"{prefix}:{resolved.relative_to(root).as_posix()}"
        return None

    def _inspect(self, path: str):
        with self._lock:
            self._timers.pop(path, None)
        file_key = self._file_key(path)
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            stat = Path(path).stat()
        except FileNotFoundError:
            with self._lock:
                self._last_hash.pop(path, None)
            self.event_manager.broadcast("file_deleted", {"path": path, "file": file_key})
            return
        except OSError as e:
            logger.error(f"Error reading changed file {path}: {e}")
            return

        digest = content_hash(text)
        with self._lock:
            if self._last_hash.get(path) == digest:
                return
            self._last_hash[path] = digest
        # An externally written version is a legitimate merge base for a later save.
        if file_key:
            version_store.put(file_key, text)

        self.event_manager.broadcast(
            "file_changed",
            {
                "path": path,
                "file": file_key,
                "hash": digest,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
            },
        )


event_manager = SSEEventManager()
