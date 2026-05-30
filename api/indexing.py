"""
MongoDB index enforcement for the Forza Telemetry API.

This module ensures all necessary indexes exist for common search and query patterns
used by the frontend and API consumers. It is called on application startup.
"""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database


def ensure_indexes(db: Database) -> None:
    """
    Create/ensure indexes needed for efficient querying of telemetry data.
    This function is idempotent and safe to call on every startup.
    It is defensive against name conflicts from the collector.
    """
    def safe_create_index(collection, keys, **kwargs):
        try:
            collection.create_index(keys, **kwargs)
        except Exception as e:
            if "Index already exists with a different name" in str(e):
                # Index with same keys but different name already exists — acceptable
                pass
            else:
                raise

    # --- sessions collection ---
    sessions: Collection = db.sessions

    safe_create_index(sessions, [("start_time", DESCENDING)])
    safe_create_index(sessions, [("car_ordinal", ASCENDING), ("start_time", DESCENDING)])
    safe_create_index(sessions, [("end_time", ASCENDING)], sparse=True)

    # --- telemetry_samples collection ---
    samples: Collection = db.telemetry_samples

    safe_create_index(samples, [("session_id", ASCENDING), ("ts", DESCENDING)])
    safe_create_index(samples, [("ts", DESCENDING)])
    safe_create_index(samples, [("session_id", ASCENDING), ("gear", ASCENDING), ("ts", DESCENDING)])
    safe_create_index(samples, [("session_id", ASCENDING), ("speed", DESCENDING)])
    safe_create_index(samples, [("session_id", ASCENDING), ("handbrake", DESCENDING), ("ts", DESCENDING)], sparse=True)

    # --- raw_packets collection ---
    raw: Collection = db.raw_packets

    safe_create_index(raw, [("session_id", ASCENDING), ("ts", DESCENDING)])
    safe_create_index(raw, [("ts", DESCENDING)])

    print("[API] MongoDB indexes ensured for search use cases.")