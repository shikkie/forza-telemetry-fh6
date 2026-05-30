"""
Car ordinal lookup system for Forza Horizon 6 telemetry.

`CarOrdinal` (from the UDP packet) is a stable integer ID that uniquely identifies
a specific car make + model + variant in FH6.

This module provides an easy way to turn those IDs into human-readable names
for the dashboard, CLI `sessions` command, and any future web UI.

## Current State of FH6 Car Ordinal Data (as of late May 2026)

After extensive searching across GitHub, Reddit, Forza forums, and community resources:

**There is currently no complete public CarOrdinal → car name database for Forza Horizon 6 available on the internet.**

FH6 is very new. While there are good community car lists (Google Sheets with make/model/class/PI/rarity), none of the public ones include the telemetry `CarOrdinal` values yet. ManteoMax’s famous spreadsheets (the gold standard in FH4/FH5) do not have a full FH6 ordinal mapping released at this time.

### Best ways to build your own mapping right now:
1. Use the collector + dashboard while driving cars → note the `car_ordinal` that appears.
2. Use the FH6-DBDUMPER tool (matkhl/FH6-DBDUMPER on GitHub) to dump the game’s internal car database while running.
3. Watch r/ForzaHorizon, r/ForzaModding, and ManteoMax’s site for updates.

The structure below is ready for you to populate as soon as you (or the community) get the numbers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# =============================================================================
# In-code car database (easy to edit)
# Add entries here for cars you frequently drive.
# =============================================================================
CAR_DB: dict[int, dict[str, Any]] = {
    # === POPULATE THIS SECTION WITH REAL ORDINALS FROM YOUR GAME ===
    #
    # Example (replace these with actual values you capture):
    # 2847: {
    #     "name": "Porsche 911 GT3 RS",
    #     "manufacturer": "Porsche",
    #     "year": 2023,
    #     "pi": 920,
    #     "class": "S2",
    # },
    # 3912: {
    #     "name": "Ferrari 488 GTB",
    #     "manufacturer": "Ferrari",
    #     "year": 2019,
    #     "pi": 885,
    #     "class": "S1",
    # },
}


def _load_external_cars() -> dict[int, dict]:
    """Optionally load from cars.json next to this file (or in project root)."""
    candidates = [
        Path(__file__).parent / "data" / "cars.json",
        Path(__file__).parent / "cars.json",
        Path.cwd() / "cars.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # Convert string keys back to int
                return {int(k): v for k, v in data.items()}
            except Exception:
                pass
    return {}


# Merge external file data (file takes precedence for overrides)
_external = _load_external_cars()
CAR_DB = {**CAR_DB, **_external}


def get_car_info(ordinal: int | None) -> dict[str, Any]:
    """Return rich info for a car ordinal. Always returns a dict."""
    if ordinal is None:
        ordinal = 0

    try:
        ordinal = int(ordinal)
    except (TypeError, ValueError):
        ordinal = 0

    return CAR_DB.get(
        ordinal,
        {
            "name": f"Unknown Car (Ordinal {ordinal})",
            "manufacturer": "Unknown",
            "year": None,
            "pi": None,
            "class": None,
            "drivetrain": None,
        },
    )


def get_car_name(ordinal: int | None) -> str:
    """Return just the display name for a car."""
    return get_car_info(ordinal).get("name", f"Car {ordinal}")


def get_car_display(ordinal: int | None) -> str:
    """Nice one-line display: 'Porsche 911 GT3 RS (S2 920)'"""
    info = get_car_info(ordinal)
    name = info.get("name", f"Car {ordinal}")
    cls = info.get("class")
    pi = info.get("pi")

    if cls and pi:
        return f"{name} ({cls} {pi})"
    if cls:
        return f"{name} ({cls})"
    return name
