"""
Debug console for inspecting live Forza Horizon 6 telemetry.

Run with:
    forza-telemetry debug

This connects to a running collector over the Unix socket and prints
every incoming telemetry packet in a readable format.

Very useful for:
- Seeing exactly what values the game is sending for buttons (handbrake, clutch, etc.)
- Spotting which fields change when you press controller inputs
- Debugging why something isn't appearing in the dashboard
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from forza_telemetry.comms import SocketClient
from forza_telemetry.config import settings

console = Console()


class DebugViewer:
    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or settings.socket_path
        self.last_data: dict[str, Any] = {}
        self.packet_count = 0

    def _build_table(self, data: dict[str, Any]) -> Table:
        table = Table(title=f"Live Telemetry Packet #{self.packet_count}", show_header=True, header_style="bold magenta")
        table.add_column("Field", style="cyan", no_wrap=True)
        table.add_column("Value", style="white")
        table.add_column("Changed", style="yellow")

        # Order fields nicely for debugging controller inputs
        priority_fields = [
            "handbrake", "clutch", "brake", "throttle", "steer",
            "gear", "gear_display", "rpm", "speed_mph", "speed_kmh",
            "is_race_on", "car_ordinal", "drivetrain",
            "power_hp", "power_kw", "torque",
            "lap_number", "race_position",
        ]

        shown = set()

        # Show priority fields first
        for key in priority_fields:
            if key in data:
                value = data[key]
                changed = "✓" if self.last_data.get(key) != value else ""
                table.add_row(key, str(value), changed)
                shown.add(key)

        # Then show everything else
        for key, value in sorted(data.items()):
            if key not in shown:
                changed = "✓" if self.last_data.get(key) != value else ""
                table.add_row(key, str(value), changed)

        return table

    def _on_message(self, msg: dict[str, Any]):
        if msg.get("type") != "telemetry":
            return

        data = msg.get("data", {})
        self.packet_count += 1
        self.last_data = data.copy()

        table = self._build_table(data)
        console.print(table)
        console.print()  # spacing

    async def run(self):
        console.rule("[bold red]Forza Horizon 6 — Raw Telemetry Debug View")
        console.print("Press buttons on your controller and watch the values change above.")
        console.print(f"Connecting to collector at {self.socket_path}...\n")

        client = SocketClient(self.socket_path, on_message=self._on_message)

        if not await client.connect(max_attempts=30, retry_delay=0.5):
            console.print("[red]Failed to connect to collector. Is it running?[/red]")
            return

        await client.start_listening()

        try:
            # Keep the process alive
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Debug viewer stopped.[/yellow]")
        finally:
            await client.close()


async def run_debug(socket_path: Path | None = None):
    viewer = DebugViewer(socket_path)
    await viewer.run()
