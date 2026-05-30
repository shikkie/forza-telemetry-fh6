"""
Configuration management using Pydantic Settings.

Loads from (in order):
1. Environment variables (highest priority)
2. .env file in CWD or specified path
3. Default values

All settings are validated at startup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with strong validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="FORZA_",  # Only some vars use prefix; we override below for clarity
    )

    # -------------------------------------------------------------------------
    # UDP Collector
    # -------------------------------------------------------------------------
    udp_host: str = Field(
        default="0.0.0.0",
        description="Interface to bind UDP listener (0.0.0.0 for all interfaces)",
    )
    udp_port: int = Field(
        default=20066,
        ge=1024,
        le=65535,
        description="UDP port the game sends telemetry to (must match in-game Data Out port)",
    )

    # -------------------------------------------------------------------------
    # MongoDB
    # -------------------------------------------------------------------------
    mongo_uri: str = Field(
        default="mongodb://localhost:27018",
        description="MongoDB connection string (use mongodb://mongo:27017 inside Docker, or localhost:27018 when connecting from host)",
    )
    mongo_db: str = Field(
        default="forza_telemetry_fh6",
        min_length=1,
        description="Database name for telemetry data",
    )

    # -------------------------------------------------------------------------
    # Inter-process communication (collector <-> dashboard)
    # -------------------------------------------------------------------------
    socket_path: Path = Field(
        default=Path("/tmp/forza-telemetry.sock"),
        description="Unix domain socket path for live dashboard attachment",
    )

    # -------------------------------------------------------------------------
    # Storage tuning
    # -------------------------------------------------------------------------
    raw_storage_interval: int = Field(
        default=1,
        ge=1,
        description="Store a raw 324-byte packet every N received packets (1 = every packet)",
    )
    parsed_storage_interval: int = Field(
        default=5,
        ge=1,
        description="Store a rich parsed telemetry document every N packets",
    )

    # -------------------------------------------------------------------------
    # Live dashboard feed
    # -------------------------------------------------------------------------
    live_update_hz: int = Field(
        default=30,
        ge=1,
        le=120,
        description="Maximum rate (Hz) at which live telemetry is pushed to attached displays",
    )

    # --- Display preferences ---
    speed_unit: Literal["kmh", "mph"] = Field(
        default="mph",
        description="Preferred speed unit in the dashboard ('kmh' or 'mph')",
    )

    power_unit: Literal["kw", "hp", "ps"] = Field(
        default="hp",
        description="Preferred power unit ('kw', 'hp', or 'ps')",
    )

    # -------------------------------------------------------------------------
    # Optional companion APIs
    # -------------------------------------------------------------------------
    fh6cardata_api: str = Field(
        default="http://localhost:3002",
        validation_alias="FH6CARDATA_API",
        description="Base URL for the external fh6cardata API (https://github.com/shikkie/fh6cardata). "
        "Used by the live dashboard to look up year/make/model by car_ordinal via /api/cars/by-ordinal/{id}. "
        "Set via FH6CARDATA_API in .env (e.g. http://localhost:3002 or a remote host).",
    )

    # -------------------------------------------------------------------------
    # Session management
    # -------------------------------------------------------------------------
    session_idle_timeout_sec: int = Field(
        default=300,
        ge=30,
        description="Close current session after this many seconds without IsRaceOn packets",
    )

    # -------------------------------------------------------------------------
    # Observability
    # -------------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )
    strict_packet_validation: bool = Field(
        default=False,
        description="Enable extra validation and logging on every packet (impacts performance)",
    )

    # -------------------------------------------------------------------------
    # Internal / derived
    # -------------------------------------------------------------------------
    @field_validator("socket_path", mode="before")
    @classmethod
    def _expand_socket_path(cls, v: str | Path) -> Path:
        p = Path(v).expanduser().resolve()
        # Ensure parent directory exists
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def live_update_interval(self) -> float:
        """Seconds between live updates sent to dashboards."""
        return 1.0 / self.live_update_hz

    def get_mongo_uri_masked(self) -> str:
        """Return Mongo URI with credentials masked for logging."""
        if "@" in self.mongo_uri:
            prefix, rest = self.mongo_uri.split("@", 1)
            if "://" in prefix:
                scheme, creds = prefix.split("://", 1)
                return f"{scheme}://***:***@{rest}"
        return self.mongo_uri


# Global settings instance (imported everywhere)
settings = Settings()


def reload_settings(env_file: str | None = None) -> Settings:
    """Reload settings (useful for tests)."""
    global settings
    settings = Settings(_env_file=env_file)
    return settings
