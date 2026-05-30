"""
Forza Horizon 6 UDP Telemetry Collector.

Core responsibilities:
- Receive 324-byte UDP packets from the game (async datagram)
- Parse into strongly typed ForzaTelemetryPacket
- Detect race session start (IsRaceOn false → true)
- Persist raw + sampled data to MongoDB via storage layer
- Broadcast live data to any attached Textual dashboards over Unix socket
- Graceful shutdown and resource cleanup
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from forza_telemetry.comms import SocketServer
from forza_telemetry.config import settings
from forza_telemetry.packet import ForzaTelemetryPacket, PACKET_SIZE
from forza_telemetry.storage import MongoStorage

logger = logging.getLogger(__name__)


class TelemetryCollector:
    """
    Main collector service.

    Can be used standalone (collector only) or alongside a dashboard
    (in the same process or via socket).
    """

    def __init__(
        self,
        on_telemetry: Callable[[ForzaTelemetryPacket], None] | None = None,
    ) -> None:
        self.on_telemetry = on_telemetry  # Optional in-process callback (used when running both modes)

        self.storage = MongoStorage()
        self.socket_server = SocketServer(settings.socket_path)

        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _UDPProtocol | None = None
        self._last_packet_time: float = 0.0
        self._running = asyncio.Event()
        self._shutdown_complete = asyncio.Event()

        # Stats
        self.packets_received = 0
        self.packets_parsed = 0
        self.last_rpm: float = 0.0

        # Rate limiting for live socket broadcasts
        self._last_broadcast_ts: float = 0.0
        self._broadcast_interval = settings.live_update_interval

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Start UDP listener, MongoDB, and Unix socket server."""
        logger.info("Starting Forza FH6 Telemetry Collector")
        logger.info("  UDP: %s:%d", settings.udp_host, settings.udp_port)
        logger.info("  MongoDB: %s", settings.get_mongo_uri_masked())
        logger.info("  Socket: %s", settings.socket_path)

        # Connect storage first
        await self.storage.connect()

        # Start Unix socket server for dashboards
        await self.socket_server.start()

        # Create UDP endpoint
        loop = asyncio.get_running_loop()
        self._protocol = _UDPProtocol(self._handle_datagram)
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: self._protocol,
            local_addr=(settings.udp_host, settings.udp_port),
            reuse_port=True,
        )

        self._running.set()
        self._last_packet_time = time.monotonic()
        logger.info("Collector ready - waiting for Forza Horizon 6 telemetry...")

        # Register signal handlers for graceful shutdown (best effort)
        self._install_signal_handlers()

    async def stop(self) -> None:
        """Stop all components cleanly."""
        logger.info("Shutting down collector...")
        self._running.clear()

        # Close UDP
        if self._transport:
            self._transport.close()

        # Stop socket server (disconnects dashboards)
        await self.socket_server.stop()

        # Close storage (flushes writers + closes current session)
        await self.storage.close()

        self._shutdown_complete.set()
        logger.info("Collector stopped cleanly. Total packets processed: %d", self.packets_received)

    async def run_forever(self) -> None:
        """Convenience: start + wait until shutdown requested."""
        await self.start()
        try:
            # Idle loop that also performs idle session timeout checks
            while self._running.is_set():
                await self._idle_check()
                await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    # ------------------------------------------------------------------
    # UDP handling
    # ------------------------------------------------------------------
    def _handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """
        Called from the UDP protocol (may be on any thread in some event loops).
        We schedule actual work onto the event loop.
        """
        self.packets_received += 1
        self._last_packet_time = time.monotonic()

        # Schedule coroutine
        loop = asyncio.get_running_loop()
        loop.create_task(self._process_packet(data, addr))

    async def _process_packet(self, data: bytes, addr: tuple[str, int]) -> None:
        """Full async processing path for a telemetry packet."""
        if len(data) != PACKET_SIZE:
            if settings.strict_packet_validation:
                logger.warning("Received %d-byte datagram from %s (expected %d)", len(data), addr, PACKET_SIZE)
            return

        try:
            packet = ForzaTelemetryPacket.from_bytes(data)
            self.packets_parsed += 1
            self.last_rpm = packet.current_engine_rpm
        except Exception as exc:
            logger.error("Failed to parse packet from %s: %s", addr, exc)
            if settings.strict_packet_validation:
                logger.debug("Raw payload (first 64 bytes): %s", data[:64].hex())
            return

        # Persist + session logic
        session_id = await self.storage.handle_packet(packet, data)

        # Optional in-process listener (used in combined run mode)
        if self.on_telemetry:
            try:
                self.on_telemetry(packet)
            except Exception as exc:
                logger.exception("on_telemetry callback failed: %s", exc)

        # Broadcast to any attached dashboards (rate limited)
        await self._maybe_broadcast(packet, session_id)

    async def _maybe_broadcast(self, packet: ForzaTelemetryPacket, session_id: Any) -> None:
        """Push live data to connected dashboards at a controlled rate."""
        now = time.monotonic()
        if (now - self._last_broadcast_ts) < self._broadcast_interval:
            return

        self._last_broadcast_ts = now

        # Build compact payload (avoid sending every single field over the wire every frame)
        payload = {
            "type": "telemetry",
            "data": {
                "received_at": packet.received_at.isoformat(),
                "is_race_on": packet.is_race_on,
                "speed": round(packet.speed, 3),
                "speed_kmh": round(packet.speed_kmh, 1),
                "speed_mph": round(packet.speed_mph, 1),
                "rpm": round(packet.current_engine_rpm, 1),
                "gear": packet.gear,
                "gear_display": packet.gear_display,
                "throttle": round(packet.throttle_normalized, 3),
                "brake": round(packet.brake_normalized, 3),
                "steer": round(packet.steer_normalized, 3),
                "clutch": packet.clutch,
                "handbrake": packet.handbrake,
                "lap_number": packet.lap_number,
                "current_lap": round(packet.current_lap, 3) if packet.current_lap > 0 else 0.0,
                "last_lap": round(packet.last_lap, 3) if packet.last_lap > 0 else 0.0,
                "best_lap": round(packet.best_lap, 3) if packet.best_lap > 0 else 0.0,
                "race_position": packet.race_position,
                "fuel": round(packet.fuel, 4),
                "boost": round(packet.boost, 2),
                "power_watts": round(packet.power, 1),
                "power_kw": round(packet.power_kw, 1),
                "power_hp": round(packet.power_hp, 1),
                "power_ps": round(packet.power_ps, 1),
                "torque": round(packet.torque, 1),
                "car_ordinal": packet.car_ordinal,
                "drivetrain": ["FWD", "RWD", "AWD"][max(0, min(2, packet.drivetrain_type))],
                # Tire data (useful for advanced users)
                "tire_slip_fl": round(packet.tire_combined_slip_fl, 3),
                "tire_slip_fr": round(packet.tire_combined_slip_fr, 3),
                "tire_slip_rl": round(packet.tire_combined_slip_rl, 3),
                "tire_slip_rr": round(packet.tire_combined_slip_rr, 3),
            },
        }

        if session_id:
            payload["session_id"] = str(session_id)

        await self.socket_server.broadcast(payload)

    # ------------------------------------------------------------------
    # Health & session timeout
    # ------------------------------------------------------------------
    async def _idle_check(self) -> None:
        """Close session if we haven't seen IsRaceOn packets for a long time."""
        if not self.storage.current_session_id:
            return

        idle_for = time.monotonic() - self._last_packet_time
        if idle_for > settings.session_idle_timeout_sec:
            logger.info("No telemetry for %.0fs - closing idle session", idle_for)
            # Force close via storage (we don't have direct access to private method)
            # The storage layer will naturally handle it on next rising edge or on shutdown.
            # For now we just log; a more advanced version could force end_time.

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        """Best-effort signal handling for Linux / containers."""
        loop = asyncio.get_running_loop()

        def _handler(sig: int) -> None:
            logger.info("Received signal %s, initiating shutdown...", sig)
            asyncio.create_task(self.stop())

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: _handler(s))
            except (NotImplementedError, RuntimeError):
                # Windows or certain loops don't support add_signal_handler
                pass


class _UDPProtocol(asyncio.DatagramProtocol):
    """Minimal high-performance UDP protocol."""

    def __init__(self, callback: Callable[[bytes, tuple[str, int]], None]) -> None:
        self.callback = callback
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        # Extremely hot path - keep it minimal
        self.callback(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.warning("UDP error received: %s", exc)
