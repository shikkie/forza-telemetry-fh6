from __future__ import annotations

from bson import ObjectId
from flask import Blueprint, jsonify, request

from api.extensions import mongo

telemetry_bp = Blueprint("telemetry", __name__)


@telemetry_bp.route("/samples", methods=["GET"])
def get_telemetry_samples():
    """
    Query telemetry samples with filtering.
    Supports: session_id, limit, skip, min_speed, max_speed, gear
    """
    limit = min(int(request.args.get("limit", 500)), 2000)
    skip = int(request.args.get("skip", 0))

    query = {}

    if session_id := request.args.get("session_id"):
        try:
            query["session_id"] = ObjectId(session_id)
        except Exception:
            return jsonify({"error": "Invalid session_id"}), 400

    if gear := request.args.get("gear"):
        try:
            query["gear"] = int(gear)
        except ValueError:
            pass

    if min_speed := request.args.get("min_speed"):
        query.setdefault("speed", {})["$gte"] = float(min_speed)
    if max_speed := request.args.get("max_speed"):
        query.setdefault("speed", {})["$lte"] = float(max_speed)

    cursor = (
        mongo.db.telemetry_samples.find(query)
        .sort("ts", -1)
        .skip(skip)
        .limit(limit)
    )

    samples = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        if "session_id" in doc:
            doc["session_id"] = str(doc["session_id"])

        # Normalize date fields
        if "ts" in doc and isinstance(doc["ts"], dict) and "$date" in doc["ts"]:
            doc["ts"] = doc["ts"]["$date"]

        samples.append(doc)

    return jsonify({
        "samples": samples,
        "count": len(samples),
        "limit": limit,
        "skip": skip
    })