"""
Typer CLI for forza-telemetry-fh6.

Commands:
  run            Collector + dashboard together
  collector      UDP collector only (the main daemon)
  dashboard      Textual live dashboard (attach to running collector)
  sessions       List stored race sessions from MongoDB
  replay         Replay a recorded race from DB over UDP (great for testing)
  export         Export a session to a portable .fh6replay file
  replay-file    Replay a .fh6replay file (no Mongo needed)

Environment variables and .env file are supported via pydantic-settings.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.logging import RichHandler

from forza_telemetry.collector import TelemetryCollector
from forza_telemetry.config import reload_settings, settings
from forza_telemetry.dashboard import run_dashboard
from forza_telemetry.cars import get_car_name
from forza_telemetry.debug import run_debug
from forza_telemetry.replay import (
    ReplayError,
    export_session_to_file,
    run_replay_from_file_sync,
    run_replay_from_mongo_sync,
)
from forza_telemetry.storage import MongoStorage

app = typer.Typer(
    name="forza-telemetry",
    help="Forza Horizon 6 UDP Telemetry Collector & Detachable Dashboard",
    add_completion=True,
    rich_markup_mode="rich",
)
console = Console()


def setup_logging(level: str | None = None) -> None:
    """Configure nice colored logging."""
    log_level = level or settings.log_level
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console, show_path=False)],
    )


@app.callback()
def main(
    ctx: typer.Context,
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to .env file (default: .env in current directory)",
        exists=False,
        dir_okay=False,
    ),
    log_level: str = typer.Option(
        None,
        "--log-level",
        "-l",
        help="Override log level (DEBUG, INFO, WARNING, ERROR)",
    ),
) -> None:
    """Global options loaded before any subcommand."""
    if config:
        reload_settings(str(config))
    if log_level:
        # Re-apply after possible reload
        pass
    setup_logging(log_level)


# ------------------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------------------

@app.command()
def run(
    udp_port: int | None = typer.Option(None, "--udp-port", "-p", help="UDP port (overrides env)"),
    mongo_uri: str | None = typer.Option(None, "--mongo", help="MongoDB URI"),
    socket: Path | None = typer.Option(None, "--socket", help="Unix socket path"),
) -> None:
    """
    Run collector + dashboard together in a single process.

    This is the easiest way to get started. The dashboard will receive
    live data directly (no socket needed internally).
    """
    _apply_overrides(udp_port, mongo_uri, socket)
    setup_logging()

    console.rule("[bold cyan]Forza Horizon 6 Telemetry — Combined Mode")
    console.print("Collector + Dashboard running together. Press [bold]Ctrl+C[/] to stop.\n")

    async def _combined() -> None:
        collector = TelemetryCollector()

        # Monkey-patch the collector's broadcast path to also feed the dashboard directly
        # For simplicity and cleanliness we start the collector, then launch dashboard
        # in the same loop. The dashboard will still use the socket (easiest).
        await collector.start()

        # Give the socket server a moment to bind
        await asyncio.sleep(0.3)

        # Run dashboard (it will connect to the local socket)
        dashboard_task = asyncio.create_task(
            run_dashboard(socket_path=settings.socket_path, in_process=True),
            name="dashboard",
        )

        try:
            await dashboard_task
        except asyncio.CancelledError:
            pass
        finally:
            await collector.stop()

    try:
        asyncio.run(_combined())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/]")
        sys.exit(0)


@app.command()
def collector(
    udp_port: int | None = typer.Option(None, "--udp-port", "-p"),
    mongo_uri: str | None = typer.Option(None, "--mongo"),
    socket: Path | None = typer.Option(None, "--socket"),
    raw_interval: int | None = typer.Option(None, "--raw-interval", help="Store raw packet every N"),
    parsed_interval: int | None = typer.Option(None, "--parsed-interval", help="Store parsed sample every N"),
) -> None:
    """
    Run ONLY the UDP collector (background friendly).

    It will:
    - Listen for FH6 telemetry on the configured UDP port
    - Auto-create MongoDB sessions on race start
    - Write raw + sampled data
    - Accept dashboard connections on the Unix socket

    You can attach dashboards later with `forza-telemetry dashboard`.
    """
    _apply_overrides(udp_port, mongo_uri, socket, raw_interval, parsed_interval)
    setup_logging()

    console.rule("[bold green]Forza Horizon 6 Telemetry Collector")
    console.print(f"UDP {settings.udp_host}:{settings.udp_port}")
    console.print(f"Mongo → {settings.get_mongo_uri_masked()}")
    console.print(f"Socket → {settings.socket_path}")
    console.print("Press Ctrl+C to stop.\n")

    async def _run_collector() -> None:
        coll = TelemetryCollector()
        try:
            await coll.run_forever()
        except KeyboardInterrupt:
            pass

    try:
        asyncio.run(_run_collector())
    except KeyboardInterrupt:
        console.print("\n[yellow]Collector stopped[/]")
        sys.exit(0)


@app.command()
def dashboard(
    socket: Path | None = typer.Option(None, "--socket", "-s", help="Path to collector socket"),
) -> None:
    """
    Launch the Textual dashboard and attach to a running collector.

    The collector must already be running (started with `collector` or `run`).
    """
    if socket:
        # We can't easily mutate the global settings object here, so we pass explicitly
        pass

    setup_logging()

    console.rule("[bold magenta]Forza Horizon 6 Telemetry Dashboard")
    console.print("Connecting to collector... (press Q to quit)\n")

    socket_path = socket or settings.socket_path

    try:
        asyncio.run(run_dashboard(socket_path=socket_path, in_process=False))
    except KeyboardInterrupt:
        pass


@app.command()
def status() -> None:
    """Quick health check (shows config and whether socket is present)."""
    setup_logging("WARNING")

    console.print("[bold]Current Configuration[/bold]\n")
    console.print(f"  UDP:        {settings.udp_host}:{settings.udp_port}")
    console.print(f"  MongoDB:    {settings.get_mongo_uri_masked()}")
    console.print(f"  Database:   {settings.mongo_db}")
    console.print(f"  Socket:     {settings.socket_path}")
    console.print(f"  Raw every:  {settings.raw_storage_interval} packet(s)")
    console.print(f"  Parsed every: {settings.parsed_storage_interval} packet(s)")
    console.print(f"  Live Hz:    {settings.live_update_hz}")

    sock = settings.socket_path
    if sock.exists():
        console.print(f"\n  [green]Socket exists[/green] — a collector is likely running")
    else:
        console.print(f"\n  [yellow]Socket not present[/yellow] — start a collector first")


@app.command()
def version() -> None:
    """Show version information."""
    from forza_telemetry import __version__

    console.print(f"forza-telemetry-fh6 [bold cyan]{__version__}[/]")


# ------------------------------------------------------------------------------
# New: Session listing + Replay / Export (test race from DB)
# ------------------------------------------------------------------------------

@app.command()
def sessions(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent sessions to show"),
) -> None:
    """List recent race sessions stored in MongoDB (most recent first)."""
    setup_logging("WARNING")

    async def _list() -> None:
        storage = MongoStorage()
        await storage.connect()
        docs = await storage.get_recent_sessions(limit=limit)

        if not docs:
            console.print("[yellow]No sessions found in the database yet.[/]")
            await storage.close()
            return

        console.rule("[bold cyan]Recent Forza Horizon 6 Sessions")
        for doc in docs:
            sid = str(doc["_id"])
            start = doc.get("start_time", "?")
            end = doc.get("end_time") or "open"
            car_ordinal = doc.get("car_ordinal", 0)
            car_name = get_car_name(car_ordinal)
            packets = doc.get("packet_count", 0)
            samples = doc.get("sample_count", 0)
            console.print(
                f"[bold]{sid}[/]\n"
                f"  Start: {start}  →  End: {end}\n"
                f"  Car: {car_name} (ID {car_ordinal})   Packets: {packets:,}   Samples: {samples:,}\n"
            )
        await storage.close()

    asyncio.run(_list())


@app.command()
def replay(
    session: str = typer.Argument(..., help="Session ID (or 'latest' for most recent race)"),
    host: str = typer.Option("127.0.0.1", "--host", help="Target UDP host for replay"),
    port: int = typer.Option(20066, "--port", "-p", help="Target UDP port"),
    speed: float = typer.Option(1.0, "--speed", "-s", help="Playback speed multiplier"),
    loop: bool = typer.Option(False, "--loop", "-l", help="Loop the replay forever"),
    only_race: bool = typer.Option(False, "--only-race", help="Only send packets where IsRaceOn was true"),
    limit: int | None = typer.Option(None, "--limit", help="Maximum number of packets to send"),
    timing: str = typer.Option(
        "wall",
        "--timing",
        help="Timing source: 'wall' (real capture time) or 'game' (in-game timestamp_ms)",
        show_choices=True,
    ),
) -> None:
    """
    Replay a recorded race from MongoDB over UDP.

    This lets you test the collector + dashboard without Forza Horizon 6 running.
    Perfect for development, demos, and automated testing.
    """
    setup_logging()

    async def _resolve_session() -> str:
        if session.lower() == "latest":
            storage = MongoStorage()
            await storage.connect()
            docs = await storage.get_recent_sessions(limit=1)
            await storage.close()
            if not docs:
                raise ReplayError("No sessions found in database")
            return str(docs[0]["_id"])
        return session

    try:
        resolved = asyncio.run(_resolve_session())

        console.rule(f"[bold green]Replaying session {resolved}")
        run_replay_from_mongo_sync(
            session_id=resolved,
            host=host,
            port=port,
            speed=speed,
            loop=loop,
            only_race_on=only_race,
            max_packets=limit,
            timing=timing,
        )
    except ReplayError as exc:
        console.print(f"[red]Replay failed:[/] {exc}")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Replay interrupted by user[/]")


@app.command()
def export(
    session: str = typer.Argument(..., help="Session ID to export"),
    output: Path = typer.Option(
        Path("race.fh6replay"),
        "--output",
        "-o",
        help="Output replay file path (.fh6replay)",
    ),
    only_race: bool = typer.Option(False, "--only-race", help="Only export packets where IsRaceOn was true"),
    limit: int | None = typer.Option(None, "--limit", help="Max packets to export"),
) -> None:
    """
    Export a session from MongoDB into a portable .fh6replay file.

    The resulting file can be replayed later with:
        forza-telemetry replay-file race.fh6replay
    without needing MongoDB running.
    """
    setup_logging("WARNING")

    try:
        export_session_to_file(
            session_id=session,
            output_path=output,
            only_race_on=only_race,
            max_packets=limit,
        )
        console.print(f"[green]Export complete.[/] Use: [bold]forza-telemetry replay-file {output}[/]")
    except ReplayError as exc:
        console.print(f"[red]Export failed:[/] {exc}")
        raise typer.Exit(1)


@app.command("replay-file")
def replay_file(
    path: Path = typer.Argument(..., exists=True, help="Path to .fh6replay file"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(20066, "--port", "-p"),
    speed: float = typer.Option(1.0, "--speed", "-s"),
    loop: bool = typer.Option(False, "--loop", "-l"),
) -> None:
    """Replay a previously exported .fh6replay file over UDP (no MongoDB required)."""
    setup_logging()

    try:
        console.rule(f"[bold green]Replaying file {path}")
        run_replay_from_file_sync(
            replay_file=path,
            host=host,
            port=port,
            speed=speed,
            loop=loop,
        )
    except ReplayError as exc:
        console.print(f"[red]Replay failed:[/] {exc}")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Replay interrupted[/]")


@app.command()
def debug(
    socket: Path | None = typer.Option(None, "--socket", "-s", help="Path to collector socket"),
) -> None:
    """
    Debug console that prints every raw telemetry packet received from the collector.

    Extremely useful for seeing exactly what values the game is sending
    when you press buttons on your controller (especially handbrake/clutch).

    Example:
        forza-telemetry debug

    Then press buttons in-game and watch the values update live.
    """
    setup_logging("WARNING")

    try:
        asyncio.run(run_debug(socket_path=socket))
    except KeyboardInterrupt:
        pass


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def _apply_overrides(
    udp_port: int | None,
    mongo_uri: str | None,
    socket: Path | None,
    raw_interval: int | None = None,
    parsed_interval: int | None = None,
) -> None:
    """Apply CLI overrides on top of loaded settings (simple but effective)."""
    if udp_port is not None:
        settings.udp_port = udp_port
    if mongo_uri is not None:
        settings.mongo_uri = mongo_uri
    if socket is not None:
        settings.socket_path = socket
    if raw_interval is not None:
        settings.raw_storage_interval = raw_interval
    if parsed_interval is not None:
        settings.parsed_storage_interval = parsed_interval


def entrypoint() -> NoReturn:
    """Console script entry point."""
    try:
        app()
    except Exception as exc:
        console.print(f"[bold red]Fatal error:[/bold red] {exc}")
        if settings.log_level == "DEBUG":
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    entrypoint()
