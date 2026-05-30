"""
Unix domain socket communication between Collector and Dashboard.

Protocol (simple and robust):
- Messages are UTF-8 JSON followed by a single newline (JSON Lines / NDJSON).
- Collector pushes messages to all connected clients.
- Clients are expected to be fast; slow clients may have messages dropped after a small buffer.

Message types sent by collector:
  {"type": "telemetry", "data": {...ForzaTelemetryPacket fields...}}
  {"type": "session", "session_id": "64f1...", "start_time": "2026-..."}
  {"type": "status", "message": "..."}

This module provides both server (inside collector) and client (inside dashboard) helpers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# Server side (Collector)
# ------------------------------------------------------------------------------

class SocketServer:
    """
    Async Unix stream socket server that broadcasts telemetry to attached dashboards.
    """

    def __init__(self, socket_path: Path, max_clients: int = 8) -> None:
        self.socket_path = socket_path
        self.max_clients = max_clients
        self._clients: set[asyncio.StreamWriter] = set()
        self._server: asyncio.Server | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Bind and start listening on the Unix socket."""
        # Clean up stale socket from previous crash
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError as exc:
                logger.warning("Could not remove stale socket %s: %s", self.socket_path, exc)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )

        # Make socket world-writable in container / dev environments (adjust as needed)
        try:
            os.chmod(self.socket_path, 0o666)
        except OSError:
            pass

        logger.info("Unix socket server listening on %s", self.socket_path)

    async def stop(self) -> None:
        """Close all client connections and the listening socket."""
        async with self._lock:
            for writer in list(self._clients):
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
            self._clients.clear()

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass
        logger.info("Unix socket server stopped")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle a new dashboard client connection."""
        if len(self._clients) >= self.max_clients:
            logger.warning("Rejecting client: max clients (%d) reached", self.max_clients)
            writer.close()
            await writer.wait_closed()
            return

        addr = writer.get_extra_info("peername", "unknown")
        self._clients.add(writer)
        logger.info("Dashboard client connected (%s). Total clients: %d", addr, len(self._clients))

        try:
            # Send a welcome / current status
            await self._send(writer, {"type": "status", "message": "connected"})

            # Keep connection alive; we only read to detect disconnects
            while not reader.at_eof():
                try:
                    await asyncio.wait_for(reader.readline(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Send a heartbeat so client knows we're alive
                    await self._send(writer, {"type": "status", "message": "heartbeat"})
        except asyncio.IncompleteReadError:
            pass
        except Exception as exc:
            logger.debug("Client read error: %s", exc)
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("Dashboard client disconnected. Remaining: %d", len(self._clients))

    async def _send(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        """Send one JSON line to a single writer."""
        try:
            data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
            writer.write(data)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            self._clients.discard(writer)
        except Exception as exc:
            logger.debug("Failed to send to client: %s", exc)
            self._clients.discard(writer)

    async def broadcast(self, payload: dict[str, Any]) -> int:
        """
        Send a message to all currently connected dashboards.
        Returns number of clients the message was successfully written to.
        """
        if not self._clients:
            return 0

        data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")

        async with self._lock:
            targets = list(self._clients)

        sent = 0
        to_remove: list[asyncio.StreamWriter] = []

        for writer in targets:
            try:
                writer.write(data)
                await writer.drain()
                sent += 1
            except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteWriteError):
                to_remove.append(writer)
            except Exception as exc:
                logger.debug("Broadcast write error: %s", exc)
                to_remove.append(writer)

        if to_remove:
            async with self._lock:
                for w in to_remove:
                    self._clients.discard(w)

        return sent

    @property
    def client_count(self) -> int:
        return len(self._clients)


# ------------------------------------------------------------------------------
# Client side (Dashboard)
# ------------------------------------------------------------------------------

class SocketClient:
    """
    Async client used by the Textual dashboard to receive live telemetry.
    """

    def __init__(self, socket_path: Path, on_message: Callable[[dict], None] | None = None) -> None:
        self.socket_path = socket_path
        self.on_message = on_message
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._connected = asyncio.Event()
        self._stop = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    async def connect(self, max_attempts: int = 30, retry_delay: float = 0.5) -> bool:
        """Attempt to connect to the collector's Unix socket (with retries)."""
        for attempt in range(1, max_attempts + 1):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(
                    path=str(self.socket_path)
                )
                self._connected.set()
                logger.info("Connected to collector at %s", self.socket_path)
                return True
            except (FileNotFoundError, ConnectionRefusedError) as exc:
                if attempt == max_attempts:
                    logger.error("Failed to connect to collector after %d attempts", max_attempts)
                    return False
                logger.debug("Collector not ready (attempt %d): %s", attempt, exc)
                await asyncio.sleep(retry_delay)
        return False

    async def start_listening(self) -> None:
        """Start background task that reads JSON lines and calls on_message."""
        if self._task:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._read_loop(), name="socket-client-reader")

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while not self._stop.is_set():
                line = await self._reader.readline()
                if not line:
                    logger.warning("Collector closed connection")
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                    if self.on_message:
                        self.on_message(msg)
                except json.JSONDecodeError as exc:
                    logger.warning("Bad JSON from collector: %s", exc)
        except Exception as exc:
            logger.error("Socket client read error: %s", exc)
        finally:
            self._connected.clear()

    async def close(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._connected.clear()
        logger.debug("Socket client closed")


# ------------------------------------------------------------------------------
# Convenience helpers
# ------------------------------------------------------------------------------

async def iter_socket_messages(socket_path: Path) -> AsyncIterator[dict[str, Any]]:
    """
    Async generator that yields messages from the collector socket.
    Useful for simple scripts or testing.
    """
    client = SocketClient(socket_path)
    if not await client.connect():
        return

    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=128)

    def _handler(msg: dict) -> None:
        try:
            queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    client.on_message = _handler
    await client.start_listening()

    try:
        while True:
            yield await queue.get()
    finally:
        await client.close()
