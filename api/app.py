"""
Flask API for viewing Forza Horizon 6 telemetry data stored in MongoDB.

Features:
- REST endpoints for sessions and telemetry samples
- Ensures proper MongoDB indexes on startup for search performance
- CORS enabled for the React frontend
"""

from __future__ import annotations

import os
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS

from api.extensions import mongo
from api.indexing import ensure_indexes
from api.routes import sessions_bp, telemetry_bp


def create_app() -> Flask:
    app = Flask(__name__)

    # Configuration
    app.config["MONGO_URI"] = os.getenv(
        "MONGO_URI", "mongodb://localhost:27018/forza_telemetry_fh6"
    )
    app.config["MONGO_DBNAME"] = os.getenv("MONGO_DBNAME", "forza_telemetry_fh6")

    # Enable CORS for the Vite React frontend
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Initialize PyMongo
    mongo.init_app(app)

    # Register blueprints
    app.register_blueprint(sessions_bp, url_prefix="/api/sessions")
    app.register_blueprint(telemetry_bp, url_prefix="/api/telemetry")

    # Ensure indexes on startup (critical for search performance)
    with app.app_context():
        db = mongo.db
        ensure_indexes(db)

    @app.route("/api/health")
    def health():
        return jsonify({
            "status": "ok",
            "service": "forza-telemetry-api",
            "time": datetime.utcnow().isoformat()
        })

    @app.route("/api/preferences")
    def preferences():
        from forza_telemetry.config import settings
        return jsonify({
            "speed_unit": settings.speed_unit,      # mph or kmh
            "power_unit": settings.power_unit       # hp, kw, or ps
        })

    @app.route("/api/")
    def root():
        return jsonify({
            "message": "Forza Horizon 6 Telemetry API",
            "endpoints": {
                "health": "/api/health",
                "sessions": "/api/sessions",
                "telemetry": "/api/telemetry"
            }
        })

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)