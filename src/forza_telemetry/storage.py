"""
MongoDB storage layer using Motor (async).

Responsibilities:
- Manage connection lifecycle
- Create collections + indexes on startup
- Session management (auto-create on IsRaceOn rising edge)
- Store raw packets (Binary) + sampled parsed telemetry documents
- Graceful handling of high ingest rates via background tasks + bounded queues
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.binary import Binary
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

from forza_telemetry.config import settings
from forza_telemetry.packet import ForzaTelemetryPacket

logger = logging.getLogger(__name__)


class MongoStorage:
    """
    Async MongoDB storage facade for Forza telemetry.

    Usage:
        storage = MongoStorage()
        await storage.connect()
        ...
        await storage.close()
    """

    def __init__(self) -> None:
        self.client: AsyncIOMotorClient | None = None
        self.db: AsyncIOMotorDatabase | None = None

        self._current_session_id: ObjectId | None = None
        self._last_is_race_on: bool = False
        self._packet_counter: int = 0
        self._session_start_wall: datetime | None = None

        # Background writer queue (raw packets can be bursty)
        self._raw_queue: asyncio.Queue[tuple[datetime, ForzaTelemetryPacket, bytes]] = asyncio.Queue(maxsize=4096)
        self._writer_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Connection & schema
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        """Connect to MongoDB and ensure collections + indexes exist."""
        if self.client is not None:
            return

        logger.info("Connecting to MongoDB: %s", settings.get_mongo_uri_masked())
        self.client = AsyncIOMotorClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=5000,
            tz_aware=True,
        )

        # Force connection
        await self.client.admin.command("ping")
        self.db = self.client[settings.mongo_db]

        await self._ensure_indexes()
        logger.info("Connected to MongoDB database '%s'", settings.mongo_db)

        # Start background writer
        self._shutdown_event.clear()
        self._writer_task = asyncio.create_task(self._raw_writer_loop(), name="mongo-raw-writer")

    async def _ensure_indexes(self) -> None:
        """Create recommended indexes for efficient queries."""
        assert self.db is not None

        # sessions
        await self.db.sessions.create_index([("start_time", DESCENDING)])
        await self.db.sessions.create_index([("end_time", DESCENDING)])

        # raw_packets - time series style access
        await self.db.raw_packets.create_index([("session_id", ASCENDING), ("ts", DESCENDING)])
        await self.db.raw_packets.create_index([("ts", DESCENDING)])
        # Compound for common "give me last N minutes of a session"
        await self.db.raw_packets.create_index([("session_id", ASCENDING), ("is_race_on", ASCENDING), ("ts", DESCENDING)])

        # telemetry_samples - for analytics / graphing
        await self.db.telemetry_samples.create_index([("session_id", ASCENDING), ("ts", DESCENDING)])
        await self.db.telemetry_samples.create_index([("ts", DESCENDING)])

        logger.debug("MongoDB indexes ensured")

    async def close(self) -> None:
        """Gracefully close writer task and MongoDB connection."""
        self._shutdown_event.set()

        if self._writer_task:
            try:
                await asyncio.wait_for(self._writer_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._writer_task.cancel()

        if self.client:
            self.client.close()
            logger.info("MongoDB connection closed")

        # Finalize any open session
        if self._current_session_id:
            await self._close_current_session()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------
    async def _create_session(self, packet: ForzaTelemetryPacket) -> ObjectId:
        """Insert a new race session document and return its _id."""
        assert self.db is not None

        doc: dict[str, Any] = {
            "start_time": datetime.now(timezone.utc),
            "end_time": None,
            "car_ordinal": packet.car_ordinal,
            "car_class": packet.car_class,
            "car_performance_index": packet.car_performance_index,
            "drivetrain_type": packet.drivetrain_type,
            "num_cylinders": packet.num_cylinders,
            "car_group": packet.car_group,
            "packet_count": 0,
            "sample_count": 0,
        }
        result = await self.db.sessions.insert_one(doc)
        session_id = result.inserted_id
        logger.info("New race session started: %s (car_ordinal=%s)", session_id, packet.car_ordinal)
        return session_id

    async def _close_current_session(self) -> None:
        """Mark the current session as ended."""
        if not self._current_session_id or self.db is None:
            return

        await self.db.sessions.update_one(
            {"_id": self._current_session_id},
            {"$set": {"end_time": datetime.now(timezone.utc)}},
        )
        logger.info("Session closed: %s", self._current_session_id)
        self._current_session_id = None

    async def handle_packet(self, packet: ForzaTelemetryPacket, raw: bytes) -> ObjectId | None:
        """
        Main entry point from collector.

        Detects session transitions and enqueues storage work.
        Returns the current (or newly created) session_id.
        """
        self._packet_counter += 1
        now = datetime.now(timezone.utc)

        # Rising edge detection for new race session
        if not self._last_is_race_on and packet.is_race_on:
            # Close previous session if one was open
            if self._current_session_id:
                await self._close_current_session()

            self._current_session_id = await self._create_session(packet)
            self._session_start_wall = now

        self._last_is_race_on = packet.is_race_on

        # If we have an active session, enqueue work
        if self._current_session_id:
            # Always update packet counter on session
            if self._packet_counter % 50 == 0 and self.db is not None:
                await self.db.sessions.update_one(
                    {"_id": self._current_session_id},
                    {"$inc": {"packet_count": 50}},
                )

            # Enqueue raw packet storage (non-blocking)
            if self._packet_counter % settings.raw_storage_interval == 0:
                try:
                    self._raw_queue.put_nowait((now, packet, raw))
                except asyncio.QueueFull:
                    logger.warning("Raw packet storage queue full - dropping packet")

            # Enqueue parsed sample (lighter, more queryable)
            if self._packet_counter % settings.parsed_storage_interval == 0:
                await self._store_sample(now, packet)

        return self._current_session_id

    async def _store_sample(self, ts: datetime, packet: ForzaTelemetryPacket) -> None:
        """Store a rich parsed document (good for dashboards / analysis)."""
        if self.db is None or not self._current_session_id:
            return

        doc = {
            "session_id": self._current_session_id,
            "ts": ts,
            "timestamp_ms": packet.timestamp_ms,
            "is_race_on": packet.is_race_on,
            "speed": packet.speed,
            "speed_kmh": packet.speed_kmh,
            "current_engine_rpm": packet.current_engine_rpm,
            "gear": packet.gear,
            "throttle": packet.throttle_normalized,
            "brake": packet.brake_normalized,
            "steer": packet.steer_normalized,
            "lap_number": packet.lap_number,
            "current_lap": packet.current_lap,
            "race_position": packet.race_position,
            "fuel": packet.fuel,
            "boost": packet.boost,
            "car_ordinal": packet.car_ordinal,
            # World position for top-down path map
            "position_x": packet.position_x,
            "position_y": packet.position_y,
            "position_z": packet.position_z,
            "yaw": packet.yaw,
            # Map combined slip to the field names the frontend grip/oversteer cards expect
            "tire_slip_fl": packet.tire_combined_slip_fl,
            "tire_slip_fr": packet.tire_combined_slip_fr,
            "tire_slip_rl": packet.tire_combined_slip_rl,
            "tire_slip_rr": packet.tire_combined_slip_rr,
            # Handbrake + clutch now properly stored (raw byte for UI compatibility + normalized)
            "handbrake": packet.handbrake,
            "handbrake_normalized": packet.handbrake_normalized,
            "clutch": packet.clutch,
            "clutch_normalized": packet.clutch_normalized,
        }

        try:
            await self.db.telemetry_samples.insert_one(doc)
            # Increment sample counter (best effort)
            await self.db.sessions.update_one(
                {"_id": self._current_session_id},
                {"$inc": {"sample_count": 1}},
            )
        except PyMongoError as exc:
            logger.error("Failed to store telemetry sample: %s", exc)

    # ------------------------------------------------------------------
    # Background writer for raw packets (decouples UDP thread from DB)
    # ------------------------------------------------------------------
    async def _raw_writer_loop(self) -> None:
        """Consume the raw queue and write Binary blobs to Mongo."""
        assert self.db is not None

        while not self._shutdown_event.is_set():
            try:
                ts, packet, raw = await asyncio.wait_for(self._raw_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if not self._current_session_id:
                self._raw_queue.task_done()
                continue

            doc = {
                "session_id": self._current_session_id,
                "ts": ts,
                "timestamp_ms": packet.timestamp_ms,
                "is_race_on": packet.is_race_on,
                "raw": Binary(raw),
                # Denormalized for convenience without unpacking
                "speed": packet.speed,
                "rpm": packet.current_engine_rpm,
                "gear": packet.gear,
                "handbrake": packet.handbrake,
                "clutch": packet.clutch,
            }

            try:
                await self.db.raw_packets.insert_one(doc)
            except PyMongoError as exc:
                logger.error("Failed to insert raw packet: %s", exc)
            finally:
                self._raw_queue.task_done()

        # Drain remaining items on shutdown (best effort)
        while not self._raw_queue.empty():
            try:
                ts, packet, raw = self._raw_queue.get_nowait()
                if self._current_session_id:
                    await self.db.raw_packets.insert_one({
                        "session_id": self._current_session_id,
                        "ts": ts,
                        "timestamp_ms": packet.timestamp_ms,
                        "is_race_on": packet.is_race_on,
                        "raw": Binary(raw),
                        "speed": packet.speed,
                        "rpm": packet.current_engine_rpm,
                        "gear": packet.gear,
                        "handbrake": packet.handbrake,
                        "clutch": packet.clutch,
                    })
                self._raw_queue.task_done()
            except Exception:
                break

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    @property
    def current_session_id(self) -> ObjectId | None:
        return self._current_session_id

    async def get_recent_sessions(self, limit: int = 20) -> list[dict]:
        """Return the most recent sessions (newest first)."""
        if self.db is None:
            return []
        cursor = self.db.sessions.find().sort("start_time", DESCENDING).limit(limit)
        return [doc async for doc in cursor]

    async def get_session_packet_count(self, session_id: ObjectId) -> int:
        if self.db is None:
            return 0
        return await self.db.raw_packets.count_documents({"session_id": session_id})

    # ------------------------------------------------------------------
    # Replay / Export helpers
    # ------------------------------------------------------------------
    async def get_session(self, session_id: ObjectId | str) -> dict | None:
        """Fetch a single session document by id."""
        if self.db is None:
            return None
        if isinstance(session_id, str):
            try:
                session_id = ObjectId(session_id)
            except Exception:
                return None
        return await self.db.sessions.find_one({"_id": session_id})

    async def iter_raw_packets(
        self,
        session_id: ObjectId | str,
        only_race_on: bool = False,
        limit: int | None = None,
    ):
        """
        Async generator yielding raw packet documents for a session.

        Yields dicts containing at minimum:
            - ts (datetime)
            - timestamp_ms (int)
            - raw (bytes)
            - is_race_on (bool)
        """
        if self.db is None:
            return

        if isinstance(session_id, str):
            try:
                session_id = ObjectId(session_id)
            except Exception:
                return

        query: dict = {"session_id": session_id}
        if only_race_on:
            query["is_race_on"] = True

        cursor = self.db.raw_packets.find(query).sort("ts", ASCENDING)
        if limit:
            cursor = cursor.limit(limit)

        async for doc in cursor:
            # Ensure 'raw' is bytes
            if "raw" in doc and hasattr(doc["raw"], "as_bytes"):
                doc["raw"] = doc["raw"].as_bytes()
            elif isinstance(doc.get("raw"), (bytes, bytearray)):
                pass
            else:
                # Fallback: skip corrupt records
                continue
            yield doc

    async def count_raw_packets(self, session_id: ObjectId | str, only_race_on: bool = False) -> int:
        if self.db is None:
            return 0
        if isinstance(session_id, str):
            try:
                session_id = ObjectId(session_id)
            except Exception:
                return 0
        query: dict = {"session_id": session_id}
        if only_race_on:
            query["is_race_on"] = True
        return await self.db.raw_packets.count_documents(query)
