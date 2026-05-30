"""
Forza Horizon 6 Telemetry Collector & Dashboard

A high-quality, production-ready UDP telemetry listener for Forza Horizon 6.
Supports MongoDB storage with intelligent session detection and a detachable
realtime Textual dashboard.
"""

__version__ = "0.1.0"
__author__ = "Forza Telemetry Contributors"

# Lazy imports to avoid circular issues during early package load
def __getattr__(name: str):
    if name == "Settings":
        from forza_telemetry.config import Settings as _Settings
        return _Settings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Settings", "__version__"]
