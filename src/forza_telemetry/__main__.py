"""
Allow running the package directly:

    python -m forza_telemetry
    python -m forza_telemetry collector
    python -m forza_telemetry dashboard
"""

from forza_telemetry.cli import entrypoint

if __name__ == "__main__":
    entrypoint()
