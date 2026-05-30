"""
Replay engine for Forza Horizon 6 telemetry.

Pulls raw 324-byte packets from MongoDB (or a file) and re-sends them
over UDP so the collector (and any attached dashboards) see a realistic
"test race" as if the game were running.

Two main modes:
  1. Replay directly from a Mongo session (most convenient)
  2. Export a session to a compact .fh6replay file, then replay the file
     (great for tests, CI, sharing specific races, no Mongo needed)

The replay engine tries hard to preserve original timing.
"""

from __future__ import annotations

import asyncio
import struct
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from forza_telemetry.config import settings
from forza_telemetry.packet import PACKET_SIZE
from forza_telemetry.storage import MongoStorage

console = Console()

# ------------------------------------------------------------------------------
# Binary replay file format (version 1)
# ------------------------------------------------------------------------------
# Header (16 bytes):
#   magic:     b"FH6REPLAY" (8 bytes)
#   version:   uint8  (1)
#   pkt_size:  uint16 (324)
#   flags:     uint8
#   reserved:  4 bytes
#
# Then N records:
#   delay_ms:  uint32   (milliseconds to sleep *before* sending this packet)
#   raw:       324 bytes
# ------------------------------------------------------------------------------

REPLAY_MAGIC = b"FH6REPLAY"
REPLAY_VERSION = 1
REPLAY_HEADER_SIZE = 16
REPLAY_RECORD_SIZE = 4 + PACKET_SIZE  # delay + packet


class ReplayError(Exception):
    pass


# ------------------------------------------------------------------------------
# Mongo-backed replay
# ------------------------------------------------------------------------------

async def iter_packets_from_mongo(
    session_id: str,
    storage: MongoStorage,
    only_race_on: bool = False,
    max_packets: int | None = None,
) -> AsyncIterator[tuple[datetime, int, bytes]]:
    """
    Yield (wall_ts, game_timestamp_ms, raw_bytes) for packets in a session.
    """
    count = 0
    async for doc in storage.iter_raw_packets(
        session_id, only_race_on=only_race_on, limit=max_packets
    ):
        raw = doc["raw"]
        if len(raw) != PACKET_SIZE:
            continue
        yield doc["ts"], doc.get("timestamp_ms", 0), raw
        count += 1
        if max_packets and count >= max_packets:
            break


async def replay_from_mongo(
    session_id: str,
    *,
    host: str = "127.0.0.1",
    port: int = 20066,
    speed: float = 1.0,
    loop: bool = False,
    only_race_on: bool = False,
    max_packets: int | None = None,
    timing: str = "wall",   # "wall" or "game"
    progress: bool = True,
) -> int:
    """
    Replay a session from MongoDB over UDP.

    Returns total packets sent.
    """
    storage = MongoStorage()
    await storage.connect()

    session = await storage.get_session(session_id)
    if not session:
        raise ReplayError(f"Session not found: {session_id}")

    total_available = await storage.count_raw_packets(session_id, only_race_on=only_race_on)
    if total_available == 0:
        console.print("[yellow]No packets found for this session (or filter).[/]")
        await storage.close()
        return 0

    console.print(
        f"[bold cyan]Replaying session[/] {session_id}\n"
        f"  Car: {session.get('car_ordinal', '?')} | "
        f"Started: {session.get('start_time')}\n"
        f"  Packets available: {total_available} | Speed: {speed}x | Timing: {timing}"
    )

    # Create UDP socket
    transport, protocol = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: asyncio.DatagramProtocol(),
        remote_addr=(host, port),
    )

    packets_sent = 0
    start_time = time.monotonic()

    try:
        while True:  # supports --loop
            last_game_ts: int | None = None
            last_wall_ts: datetime | None = None
            first_packet = True

            packet_iter = iter_packets_from_mongo(
                session_id, storage, only_race_on=only_race_on, max_packets=max_packets
            )

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
                disable=not progress,
            ) as progress_bar:
                task = progress_bar.add_task(
                    "Replaying race...", total=total_available if not loop else None
                )

                async for wall_ts, game_ts, raw in packet_iter:
                    # Calculate how long to wait before sending this packet
                    delay = 0.0

                    if not first_packet:
                        if timing == "game" and last_game_ts is not None:
                            game_delta_ms = game_ts - last_game_ts
                            if game_delta_ms > 0:
                                delay = (game_delta_ms / 1000.0) / speed
                        elif last_wall_ts is not None:
                            wall_delta = (wall_ts - last_wall_ts).total_seconds()
                            if wall_delta > 0:
                                delay = wall_delta / speed

                    if delay > 0:
                        await asyncio.sleep(min(delay, 2.0))  # safety cap

                    # Send the packet
                    transport.sendto(raw)
                    packets_sent += 1
                    progress_bar.update(task, advance=1)

                    last_game_ts = game_ts
                    last_wall_ts = wall_ts
                    first_packet = False

            if not loop:
                break

            console.print("[dim]Looping replay...[/]")
            await asyncio.sleep(0.5)

    finally:
        transport.close()
        await storage.close()

    duration = time.monotonic() - start_time
    console.print(
        f"\n[green]Replay complete.[/] Sent {packets_sent} packets in {duration:.1f}s "
        f"({packets_sent / max(duration, 0.001):.1f} pkt/s at {speed}x)"
    )
    return packets_sent


# ------------------------------------------------------------------------------
# File-based export / replay (portable, no Mongo required at replay time)
# ------------------------------------------------------------------------------

def export_session_to_file(
    session_id: str,
    output_path: Path | str,
    *,
    only_race_on: bool = False,
    max_packets: int | None = None,
) -> int:
    """
    Export a session from MongoDB into a compact binary replay file.
    Returns number of packets written.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    async def _do_export() -> int:
        storage = MongoStorage()
        await storage.connect()

        session = await storage.get_session(session_id)
        if not session:
            raise ReplayError(f"Session not found: {session_id}")

        count = 0
        last_ts: datetime | None = None

        with open(output_path, "wb") as f:
            # Write header
            header = struct.pack(
                f"<8s B H B 4x",
                REPLAY_MAGIC,
                REPLAY_VERSION,
                PACKET_SIZE,
                0,  # flags
            )
            f.write(header)

            async for wall_ts, _game_ts, raw in iter_packets_from_mongo(
                session_id, storage, only_race_on=only_race_on, max_packets=max_packets
            ):
                delay_ms = 0
                if last_ts is not None:
                    delta = (wall_ts - last_ts).total_seconds()
                    delay_ms = max(0, int(delta * 1000))

                record = struct.pack(f"<I {PACKET_SIZE}s", delay_ms, raw)
                f.write(record)

                last_ts = wall_ts
                count += 1

        await storage.close()
        return count

    written = asyncio.run(_do_export())
    console.print(f"[green]Exported[/] {written} packets → {output_path}")
    return written


async def replay_from_file(
    replay_file: Path | str,
    *,
    host: str = "127.0.0.1",
    port: int = 20066,
    speed: float = 1.0,
    loop: bool = False,
    progress: bool = True,
) -> int:
    """
    Replay a .fh6replay file over UDP.
    """
    replay_file = Path(replay_file)
    if not replay_file.exists():
        raise ReplayError(f"Replay file not found: {replay_file}")

    data = replay_file.read_bytes()

    # Parse header
    if len(data) < REPLAY_HEADER_SIZE:
        raise ReplayError("Invalid replay file (too small)")

    magic, version, pkt_size, _flags = struct.unpack_from("<8s B H B", data, 0)

    if magic != REPLAY_MAGIC:
        raise ReplayError(f"Not a valid FH6 replay file (bad magic)")
    if version != REPLAY_VERSION:
        raise ReplayError(f"Unsupported replay version: {version}")
    if pkt_size != PACKET_SIZE:
        raise ReplayError(f"Replay packet size mismatch: {pkt_size} (expected {PACKET_SIZE})")

    records = data[REPLAY_HEADER_SIZE:]
    if len(records) % REPLAY_RECORD_SIZE != 0:
        raise ReplayError("Corrupt replay file (record size mismatch)")

    num_packets = len(records) // REPLAY_RECORD_SIZE
    console.print(f"[bold]Replaying file[/] {replay_file.name} ({num_packets} packets @ {speed}x)")

    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: asyncio.DatagramProtocol(),
        remote_addr=(host, port),
    )

    packets_sent = 0
    start = time.monotonic()

    try:
        while True:
            offset = 0
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                console=console,
                disable=not progress,
            ) as pbar:
                task = pbar.add_task("Replaying...", total=num_packets)

                while offset < len(records):
                    delay_ms, raw = struct.unpack_from(
                        f"<I {PACKET_SIZE}s", records, offset
                    )
                    offset += REPLAY_RECORD_SIZE

                    if delay_ms > 0:
                        await asyncio.sleep((delay_ms / 1000.0) / speed)

                    transport.sendto(raw)
                    packets_sent += 1
                    pbar.update(task, advance=1)

            if not loop:
                break
            console.print("[dim]Looping file replay...[/]")
            await asyncio.sleep(0.3)

    finally:
        transport.close()

    dur = time.monotonic() - start
    console.print(f"[green]Done.[/] Sent {packets_sent} packets in {dur:.1f}s")
    return packets_sent


# ------------------------------------------------------------------------------
# Convenience sync wrappers (for CLI)
# ------------------------------------------------------------------------------

def run_replay_from_mongo_sync(**kwargs) -> int:
    return asyncio.run(replay_from_mongo(**kwargs))


def run_replay_from_file_sync(**kwargs) -> int:
    return asyncio.run(replay_from_file(**kwargs))
