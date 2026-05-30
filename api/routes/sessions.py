from __future__ import annotations

from bson import ObjectId
from flask import Blueprint, jsonify, request
from flask_pymongo import PyMongo

from api.extensions import mongo

sessions_bp = Blueprint("sessions", __name__)


def _serialize_session(doc):
    if not doc:
        return None
    doc["_id"] = str(doc["_id"])

    from datetime import datetime as dt

    for field in ["start_time", "end_time"]:
        if field not in doc:
            continue
        val = doc[field]
        if isinstance(val, dt):
            doc[field] = val.isoformat()
        elif isinstance(val, dict) and "$date" in val:
            doc[field] = val["$date"]

    return doc


@sessions_bp.route("/", methods=["GET"], strict_slashes=False)
def list_sessions():
    """List sessions with basic filtering and pagination."""
    limit = min(int(request.args.get("limit", 50)), 200)
    skip = int(request.args.get("skip", 0))

    query = {}
    if car_ordinal := request.args.get("car_ordinal"):
        try:
            query["car_ordinal"] = int(car_ordinal)
        except ValueError:
            pass

    cursor = (
        mongo.db.sessions.find(query)
        .sort("start_time", -1)
        .skip(skip)
        .limit(limit)
    )

    sessions = [_serialize_session(s) for s in cursor]
    total = mongo.db.sessions.count_documents(query)

    return jsonify({
        "sessions": sessions,
        "total": total,
        "limit": limit,
        "skip": skip
    })


@sessions_bp.route("/<session_id>", methods=["GET"])
def get_session(session_id: str):
    """Get a single session by ID."""
    try:
        oid = ObjectId(session_id)
    except Exception:
        return jsonify({"error": "Invalid session ID"}), 400

    session = mongo.db.sessions.find_one({"_id": oid})
    if not session:
        return jsonify({"error": "Session not found"}), 404

    # Add some stats
    sample_count = mongo.db.telemetry_samples.count_documents({"session_id": oid})
    raw_count = mongo.db.raw_packets.count_documents({"session_id": oid})

    data = _serialize_session(session)
    data["sample_count"] = sample_count
    data["raw_packet_count"] = raw_count

    return jsonify(data)