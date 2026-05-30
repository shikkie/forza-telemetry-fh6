"""
Forza Horizon 6 Realtime Telemetry Dashboard (Textual)

Beautiful, high-refresh live dashboard that can attach to a running collector
via Unix socket or be run together with the collector in a single process.

Run modes supported:
- `forza-telemetry`               → collector + dashboard together
- `forza-telemetry dashboard`     → dashboard only (attaches to collector socket)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from rich.console import RenderResult
from rich.panel import Panel
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ProgressBar, Static

from forza_telemetry.cars import get_car_name
from forza_telemetry.comms import SocketClient
from forza_telemetry.config import settings
from forza_telemetry.packet import ForzaTelemetryPacket

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore


logger = logging.getLogger(__name__)


# =============================================================================
# Rich renderables for custom gauges
# =============================================================================

class SteeringIndicator(Static):
    """Visual steering wheel / steering input indicator."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.value: float = 0.0  # -1.0 ... +1.0

    def update_value(self, steer_normalized: float) -> None:
        self.value = max(-1.0, min(1.0, steer_normalized))
        self.refresh()

    def render(self) -> RenderResult:
        width = 28
        center = width // 2
        pos = int(center + self.value * (center - 2))

        bar = ["─"] * width
        bar[center] = "│"
        if 0 <= pos < width:
            bar[pos] = "●"

        left_label = "LEFT" if self.value < -0.15 else ""
        right_label = "RIGHT" if self.value > 0.15 else ""

        line1 = "".join(bar)
        line2 = f"{' ' * (pos - 1)}▲" if -1 < pos < width else ""

        text = Text()
        text.append("◀ ", style="cyan")
        text.append(line1, style="white")
        text.append(" ▶", style="cyan")
        text.append("\n")
        text.append(f"Steering: {self.value:+.2f}", style="bold cyan")
        return text


class TireSlipGrid(Static):
    """2x2 grid showing combined tire slip for each wheel."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.slips: dict[str, float] = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}

    def update_slips(self, fl: float, fr: float, rl: float, rr: float) -> None:
        self.slips = {"fl": fl, "fr": fr, "rl": rl, "rr": rr}
        self.refresh()

    def render(self) -> RenderResult:
        def color_for(slip: float) -> str:
            if abs(slip) < 0.6:
                return "green"
            if abs(slip) < 1.0:
                return "yellow"
            return "red"

        fl = f"[{color_for(self.slips['fl'])}]FL {self.slips['fl']:+.2f}[/]"
        fr = f"[{color_for(self.slips['fr'])}]FR {self.slips['fr']:+.2f}[/]"
        rl = f"[{color_for(self.slips['rl'])}]RL {self.slips['rl']:+.2f}[/]"
        rr = f"[{color_for(self.slips['rr'])}]RR {self.slips['rr']:+.2f}[/]"

        return Text.from_markup(f"{fl}   {fr}\n{rl}   {rr}")


# =============================================================================
# Main Dashboard App
# =============================================================================

class ForzaDashboard(App):
    """
    Textual application for live Forza Horizon 6 telemetry.
    """

    CSS = """
    Screen {
        background: #0a0a0f;
    }

    #main {
        padding: 1 2;
    }

    .panel {
        border: round #334455;
        background: #111118;
        padding: 0 1;
        margin: 0 1 1 0;
    }

    .big-number {
        text-style: bold;
        color: #00ffcc;
        text-align: center;
    }

    .label {
        color: #8899aa;
        text-align: center;
    }

    .metric {
        color: #aaddff;
    }

    .danger {
        color: #ff5555;
    }

    .good {
        color: #55ff99;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "reset_stats", "Reset Stats", show=True),
        Binding("c", "check_ordinal", "Check Ordinal", show=True),
        Binding("s", "toggle_socket", "Reconnect", show=False),
    ]

    # Reactive state
    current_packet: reactive[dict[str, Any] | None] = reactive(None)
    connected: reactive[bool] = reactive(False)
    session_id: reactive[str | None] = reactive(None)

    def __init__(self, socket_path: Path | None = None, in_process_source: bool = False) -> None:
        super().__init__()
        self.socket_path = socket_path or settings.socket_path
        self.in_process_source = in_process_source
        self.socket_client: SocketClient | None = None
        self._update_count = 0

        # In-memory cache for external car data (fh6cardata API) — lifetime of this dashboard process only.
        # We only cache *successful* lookups. Failed lookups ("no such car") are intentionally
        # NOT cached so we will retry the API on the next sighting of that ordinal
        # (in case the fh6cardata companion DB has been updated since).
        self._car_cache: dict[int, dict] = {}          # ordinal -> car info (only successful)
        self._car_lookup_pending: set[int] = set()     # ordinals we are currently fetching
        self._last_ordinal: int | None = None
        self._car_client: "httpx.AsyncClient | None" = None

        # Do NOT set self._start_time — Textual's App base class uses it internally
        # as a float from perf_counter(). Overwriting it causes a crash on startup.

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Container(id="main"):
            # === TOP SECTION: Three columns side by side ===
            with Horizontal():
                # LEFT: Throttle / Brake controls
                with Vertical(classes="panel", id="controls"):
                    yield Label("THROTTLE", classes="label")
                    yield ProgressBar(id="throttle-bar", total=1.0, show_eta=False)
                    yield Label("", id="throttle-val", classes="metric")

                    yield Label("BRAKE", classes="label")
                    yield ProgressBar(id="brake-bar", total=1.0, show_eta=False)
                    yield Label("", id="brake-val", classes="metric")

                    yield Label("CLUTCH", classes="label")
                    yield ProgressBar(id="clutch-bar", total=1.0, show_eta=False)
                    yield Label("", id="clutch-val", classes="metric")

                    yield Label("HANDBRAKE", classes="label")
                    yield Label("[dim]OFF[/]", id="handbrake-status", classes="metric")

                # CENTER: Big speed + RPM/Gear
                with Vertical():
                    with Vertical(classes="panel", id="speed-panel"):
                        yield Label("SPEED", classes="label")
                        yield Label("0", id="speed", classes="big-number")
                        yield Label("mph", id="speed-unit", classes="label")

                    with Vertical(classes="panel", id="rpm-panel"):
                        yield Label("RPM", classes="label")
                        yield Label("0000", id="rpm", classes="big-number")
                        yield Label("Gear: --", id="gear", classes="big-number")

                # RIGHT: Race info
                with Vertical(classes="panel", id="info"):
                    yield Label("RACE INFO", classes="label")
                    yield Label("Car: Unknown", id="car-name")   # populated from cars.py
                    yield Label("Lap: --", id="lap")
                    yield Label("Position: --", id="position")
                    yield Label("Current: 0:00.000", id="current-lap")
                    yield Label("Last:    0:00.000", id="last-lap")
                    yield Label("Best:    0:00.000", id="best-lap")
                    yield Label("", id="fuel-boost")
                    yield Label("", id="power-torque")

            # === BOTTOM SECTION: Tires + Steering ===
            with Horizontal():
                with Vertical(classes="panel", id="tires"):
                    yield Label("TIRE SLIP (Combined)", classes="label")
                    yield TireSlipGrid(id="tire-grid")

                with Vertical(classes="panel", id="steering"):
                    yield SteeringIndicator(id="steering-indicator")

        yield Footer()

    async def on_mount(self) -> None:
        """Start background connection + UI refresh timer."""
        self.title = "Forza Horizon 6 Telemetry"
        self.sub_title = "Waiting for data..."

        # Start a fast refresh timer (even if no data, keeps UI responsive)
        self.set_interval(1.0 / 20, self._refresh_ui, name="ui-refresh")

        if not self.in_process_source:
            self._connect_to_collector()
        else:
            self.connected = True
            self.sub_title = "In-process collector"

        # Prepare async HTTP client for optional car enrichment (only if httpx is available)
        if httpx is not None:
            self._car_client = httpx.AsyncClient(timeout=2.5, follow_redirects=True)

    async def on_unmount(self) -> None:
        """Clean up the car enrichment HTTP client."""
        if self._car_client is not None:
            await self._car_client.aclose()

    def _connect_to_collector(self) -> None:
        """Start background task that connects (and reconnects) to the collector socket."""
        self.socket_client = SocketClient(
            self.socket_path,
            on_message=self._handle_socket_message,
        )

        async def _connection_worker() -> None:
            while True:
                if await self.socket_client.connect(max_attempts=5, retry_delay=0.8):
                    self.connected = True
                    self.sub_title = f"Connected to collector @ {self.socket_path.name}"
                    await self.socket_client.start_listening()
                    break
                else:
                    self.connected = False
                    self.sub_title = "Collector not found - retrying..."
                    await asyncio.sleep(2.0)

        # Use run_worker (the proper Textual way) instead of the fragile nested @work
        self.run_worker(_connection_worker(), name="collector-connection")

    def _handle_socket_message(self, msg: dict[str, Any]) -> None:
        """Called from the socket reader thread/task."""
        if msg.get("type") == "telemetry":
            self.current_packet = msg.get("data", {})
            if "session_id" in msg:
                self.session_id = msg["session_id"]
        elif msg.get("type") == "session":
            self.session_id = msg.get("session_id")

    async def _fetch_car_enrichment(self, ordinal: int) -> None:
        """Fire-and-forget lookup against the external fh6cardata API.

        Only *successful* results are cached. Failed lookups (unknown ordinal / 404)
        are deliberately NOT cached. This means we will automatically retry the
        API the next time we see that car_ordinal (in case the fh6cardata
        companion database has been updated with the mapping since the last attempt).
        """
        if httpx is None or self._car_client is None:
            return

        # Already have it or already fetching it right now
        if ordinal in self._car_cache or ordinal in self._car_lookup_pending:
            return

        self._car_lookup_pending.add(ordinal)

        base = settings.fh6cardata_api.rstrip("/")
        url = f"{base}/api/cars/by-ordinal/{ordinal}"

        try:
            resp = await self._car_client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                self._car_cache[ordinal] = data
                logger.debug("Enriched car ordinal %s via fh6cardata API", ordinal)
            # Do NOT cache failures. We want to re-check on the next sighting.
        except Exception as exc:
            logger.debug("fh6cardata API lookup failed for ordinal %s: %s", ordinal, exc)
            # Intentionally do nothing — we will try again next time we see this ordinal
        finally:
            self._car_lookup_pending.discard(ordinal)


    def _refresh_ui(self) -> None:
        """Update all visible widgets from current_packet (called at ~20Hz)."""
        pkt = self.current_packet
        if not pkt:
            return

        self._update_count += 1

        # Speed (respect user preference)
        speed_label = self.query_one("#speed", Label)
        unit_label = self.query_one("#speed-unit", Label)

        if settings.speed_unit == "mph":
            speed_val = pkt.get("speed_mph", pkt.get("speed_kmh", 0) * 0.621371)
            unit_label.update("mph")
        else:
            speed_val = pkt.get("speed_kmh", 0)
            unit_label.update("km/h")

        speed_label.update(f"{speed_val:.0f}")

        # RPM + Gear
        rpm_label = self.query_one("#rpm", Label)
        rpm = pkt.get("rpm", 0)
        rpm_label.update(f"{rpm:4.0f}")

        gear_label = self.query_one("#gear", Label)
        gear_str = pkt.get("gear_display", "?")
        gear_label.update(f"Gear: {gear_str}")

        # Throttle / Brake
        throttle_bar = self.query_one("#throttle-bar", ProgressBar)
        brake_bar = self.query_one("#brake-bar", ProgressBar)

        throttle = pkt.get("throttle", 0.0)
        brake = pkt.get("brake", 0.0)

        throttle_bar.update(progress=throttle)
        brake_bar.update(progress=brake)

        self.query_one("#throttle-val", Label).update(f"{throttle * 100:5.1f}%")
        self.query_one("#brake-val", Label).update(f"{brake * 100:5.1f}%")

        # Clutch (analog)
        clutch = pkt.get("clutch", 0) / 255.0
        self.query_one("#clutch-bar", ProgressBar).update(progress=clutch)
        self.query_one("#clutch-val", Label).update(f"{clutch * 100:5.1f}%")

        # Handbrake - pure boolean (A button on controller for e-brake drifting)
        handbrake_raw = pkt.get("handbrake", 0)
        handbrake_on = handbrake_raw > 50

        hb_label = self.query_one("#handbrake-status", Label)
        if handbrake_on:
            hb_label.update("[bold red reverse]  HANDBRAKE  [/] [red]E-BRAKE[/]")
        else:
            hb_label.update("[dim]OFF[/]")

        # Race info - car with optional enrichment from external fh6cardata API
        car_ordinal = pkt.get("car_ordinal", 0)

        # Detect ordinal change → trigger (non-blocking) lookup if needed
        if car_ordinal and car_ordinal != self._last_ordinal:
            self._last_ordinal = car_ordinal
            # Only fetch if we don't already have it cached AND we're not already fetching it
            if car_ordinal not in self._car_cache and car_ordinal not in self._car_lookup_pending:
                asyncio.create_task(self._fetch_car_enrichment(car_ordinal))

        # Prefer rich data from the companion API if we have it cached
        car_info = self._car_cache.get(car_ordinal)
        if car_info:
            # The fh6cardata response has excellent fields: full_name, year, manufacturer, model
            display = car_info.get("full_name") or " ".join(
                str(x) for x in [car_info.get("year"), car_info.get("manufacturer"), car_info.get("model")] if x
            ).strip()
            # Always include the ordinal ID — it's the stable value from the game telemetry
            self.query_one("#car-name", Label).update(f"Car: {display} ({car_ordinal})")
        else:
            # Fallback to the existing local cars.py logic
            car_name = get_car_name(car_ordinal)
            self.query_one("#car-name", Label).update(f"Car: {car_name}")

        self.query_one("#lap", Label).update(f"Lap: {pkt.get('lap_number', 0)}")
        self.query_one("#position", Label).update(f"Position: {pkt.get('race_position', 0)}")

        def fmt_lap(t: float) -> str:
            if t <= 0:
                return "0:00.000"
            m = int(t // 60)
            s = t % 60
            return f"{m}:{s:06.3f}"

        self.query_one("#current-lap", Label).update(f"Current: {fmt_lap(pkt.get('current_lap', 0))}")
        self.query_one("#last-lap", Label).update(f"Last:    {fmt_lap(pkt.get('last_lap', 0))}")
        self.query_one("#best-lap", Label).update(f"Best:    {fmt_lap(pkt.get('best_lap', 0))}")

        # Fuel / Boost / Power (respect user power_unit setting)
        fuel = pkt.get("fuel", 0.0)
        boost = pkt.get("boost", 0.0)
        torque = pkt.get("torque", 0.0)

        unit = settings.power_unit.lower()
        if unit == "hp":
            power_val = pkt.get("power_hp", 0.0)
            power_str = f"{power_val:6.1f} hp"
        elif unit == "ps":
            power_val = pkt.get("power_ps", 0.0)
            power_str = f"{power_val:6.1f} PS"
        else:
            power_val = pkt.get("power_kw", 0.0)
            power_str = f"{power_val:6.1f} kW"

        fuel_color = "red" if fuel < 0.15 else "green"
        self.query_one("#fuel-boost", Label).update(
            f"Fuel: [{fuel_color}]{fuel*100:5.1f}%[/]   Boost: {boost:5.2f} PSI"
        )
        self.query_one("#power-torque", Label).update(
            f"Power: {power_str}   Torque: {torque:6.1f} Nm"
        )

        # Tires
        tire_grid = self.query_one("#tire-grid", TireSlipGrid)
        tire_grid.update_slips(
            pkt.get("tire_slip_fl", 0),
            pkt.get("tire_slip_fr", 0),
            pkt.get("tire_slip_rl", 0),
            pkt.get("tire_slip_rr", 0),
        )

        # Steering
        steer = self.query_one("#steering-indicator", SteeringIndicator)
        steer.update_value(pkt.get("steer", 0.0))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_reset_stats(self) -> None:
        self._update_count = 0
        self.notify("Stats reset")

    def action_toggle_socket(self) -> None:
        if self.socket_client:
            asyncio.create_task(self._reconnect())
        else:
            self.notify("No socket client (running in-process?)")

    def action_check_ordinal(self) -> None:
        """Force an immediate re-lookup of the current car's ordinal via the fh6cardata API.

        This bypasses the in-memory cache (useful for retrying ordinals that previously
        returned "no such car" after the external database has been updated).
        """
        pkt = self.current_packet or {}
        ordinal = pkt.get("car_ordinal", 0)

        if not ordinal:
            self.notify("No car ordinal available yet", severity="warning")
            return

        # Remove any previous result so we force a fresh check
        self._car_cache.pop(ordinal, None)
        self._car_lookup_pending.discard(ordinal)

        if httpx is not None and self._car_client is not None:
            asyncio.create_task(self._fetch_car_enrichment(ordinal))
            self.notify(f"Re-checking ordinal {ordinal} via fh6cardata API...")
        else:
            self.notify("External car API lookup not available (httpx not loaded)", severity="error")

    async def _reconnect(self) -> None:
        if self.socket_client:
            await self.socket_client.close()
        self.sub_title = "Reconnecting..."
        await self._connect_to_collector()

    async def on_unmount(self) -> None:
        if self.socket_client:
            await self.socket_client.close()


# =============================================================================
# Public entry point used by CLI
# =============================================================================

async def run_dashboard(
    socket_path: Path | None = None,
    in_process: bool = False,
) -> None:
    """
    Launch the Textual dashboard.

    If `in_process=True`, the dashboard expects the caller to feed it data
    via the `current_packet` reactive (advanced use).
    """
    app = ForzaDashboard(socket_path=socket_path, in_process_source=in_process)
    await app.run_async()
