# Forza Horizon 6 Telemetry Collector & Dashboard

**High-quality, production-ready UDP telemetry listener for Forza Horizon 6 (324-byte packets).**

> **For AI coding agents**: See [AGENTS.md](./AGENTS.md) for how to work effectively in this repo (especially using `./dev.sh` to manage services).

- Clean Pydantic v2 models from the official FH6 Data Out spec
- Automatic race session detection (IsRaceOn rising edge)
- MongoDB storage with raw binary packets + queryable sampled telemetry
- Detachable realtime **Textual** dashboard (attach/detach while collector keeps running)
- Asyncio throughout + Unix socket IPC
- Docker + docker-compose ready (includes MongoDB)
- Fully configurable via `.env` + CLI flags (Typer)
- Optional car details enrichment (year / make / model) via the [fh6cardata companion API](https://github.com/shikkie/fh6cardata) (see `FH6CARDATA_API` in `.env`)

---

## Features

| Feature                    | Status     | Notes |
|---------------------------|------------|-------|
| 324-byte FH6 packet parser | ✅        | Official structure |
| Session auto-detection     | ✅        | `IsRaceOn` False→True |
| Raw + sampled storage      | ✅        | `raw_packets` + `telemetry_samples` |
| Motor (async MongoDB)      | ✅        | Background writers |
| Detachable Textual UI      | ✅        | Beautiful live gauges |
| Unix socket comms          | ✅        | Multiple dashboards possible |
| Docker + Compose           | ✅        | One-command Mongo + collector |
| Graceful shutdown          | ✅        | Ctrl+C everywhere |
| Config via .env + CLI      | ✅        | pydantic-settings |

---

## Quick Start (Recommended)

### 1. Clone & Install

```bash
git clone https://github.com/yourname/forza-telemetry-fh6.git
cd forza-telemetry-fh6

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env if you want non-default Mongo URI or port
```

### 3. Start Everything

```bash
forza-telemetry run
```

This starts the **collector + dashboard together**.

### 4. In-Game Setup (Forza Horizon 6)

1. Go to **SETTINGS → HUD AND GAMEPLAY**
2. Turn **Data Out** = **On**
3. Set **Data Out IP Address** to `127.0.0.1` (or your machine IP)
4. Set **Data Out IP Port** to `20066`
5. Save and drive

You should immediately see live data in the dashboard.

---

## Running Modes

### Combined (easiest)
```bash
forza-telemetry run
```

### Collector only (recommended for long sessions / servers)
```bash
forza-telemetry collector
```

You can now attach as many dashboards as you want from the same or other machines (as long as they can reach the Unix socket or you forward it).

### Dashboard only (attach later)
```bash
forza-telemetry dashboard
```

### Docker (best for production)

```bash
# Start MongoDB + collector
docker compose up -d

# View logs
docker compose logs -f telemetry

# Attach a dashboard from the host (or another terminal)
docker compose run --rm dashboard
```

The UDP port 20066 is published, so you can point the game at your Docker host IP.

---

## Architecture

```
┌──────────────────────────────┐
│   Forza Horizon 6 (PC)       │
│   UDP 20066 (324-byte)       │
└──────────────┬───────────────┘
               │
               ▼
┌────────────────────────────────────────────────────────────┐
│                    TelemetryCollector                       │
│  ┌─────────────┐   ┌──────────────┐   ┌─────────────────┐  │
│  │ UDP Receiver│──▶│ Session Mgr  │──▶│ MongoStorage    │  │
│  └─────────────┘   └──────────────┘   │  - raw_packets  │  │
│        │                                │  - telemetry   │  │
│        │                                │  - sessions    │  │
│        ▼                                └─────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │           Unix Socket Server (/tmp/forza-*.sock)     │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
                              │
                              │ (JSON lines)
               ┌──────────────┴──────────────┐
               ▼                             ▼
        Textual Dashboard A           Textual Dashboard B
        (same machine or remote)      (can be attached later)
```

---

## MongoDB Schema

### `sessions`
```json
{
  "_id": ObjectId,
  "start_time": ISODate,
  "end_time": ISODate | null,
  "car_ordinal": 1234,
  "car_class": 5,
  "car_performance_index": 850,
  "drivetrain_type": 1,
  "packet_count": 84732,
  "sample_count": 16946
}
```

### `raw_packets`
Every raw 324-byte packet (configurable sampling):
```json
{
  "session_id": ObjectId,
  "ts": ISODate,
  "timestamp_ms": 123456789,
  "is_race_on": true,
  "raw": Binary,          // the original 324 bytes
  "speed": 42.7,
  "rpm": 6123,
  "gear": 4
}
```

### `telemetry_samples`
Rich parsed documents (much easier to query):
```json
{
  "session_id": ObjectId,
  "ts": ISODate,
  "speed_kmh": 153.7,
  "current_engine_rpm": 6123,
  "gear": 4,
  "throttle": 0.87,
  "brake": 0.0,
  "lap_number": 3,
  ...
}
```

---

## CLI Reference

```bash
forza-telemetry --help
forza-telemetry run
forza-telemetry collector --udp-port 20066
forza-telemetry dashboard --socket /tmp/forza-telemetry.sock
forza-telemetry status
```

All options can also be set via environment variables (see `.env.example`).

### Testing & Replay

One of the most powerful features is the ability to **replay real races** from the database without needing Forza running:

```bash
# See what races you have recorded
forza-telemetry sessions

# Replay the most recent race at 1.5x speed (sends UDP packets to localhost)
forza-telemetry replay latest --speed 1.5

# Replay a specific session, only during actual racing (skips menus)
forza-telemetry replay 665f1a2b3c... --only-race --speed 1.0

# Export a race to a portable file (great for tests / CI / sharing)
forza-telemetry export 665f1a2b3c... --output my_test_race.fh6replay

# Replay the exported file later (no MongoDB required)
forza-telemetry replay-file my_test_race.fh6replay --loop --speed 2.0
```

This is extremely useful for:
- Developing the collector and dashboard without launching the game
- Creating deterministic test scenarios
- Demonstrations and automated testing

---

## Configuration

Priority (highest first):
1. CLI flags
2. Environment variables
3. `.env` file
4. Defaults

Important tunables:

| Variable                    | Default          | Meaning |
|----------------------------|------------------|---------|
| `FORZA_UDP_PORT`           | 20066            | Must match in-game |
| `MONGO_URI`                | localhost:27018  | Use `mongodb://mongo:27017` inside Docker containers.<br>Use `mongodb://localhost:27018` when running the app on host while Mongo is in Docker. |
| `RAW_STORAGE_INTERVAL`     | 1                | Store raw packet every N (volume warning) |
| `PARSED_STORAGE_INTERVAL`  | 5                | ~12 Hz at 60 fps — good default |
| `LIVE_UPDATE_HZ`           | 30               | Max dashboard refresh rate |

> **Warning**: Storing every raw packet (`RAW_STORAGE_INTERVAL=1`) at 60 fps will generate ~1.3 GB per hour. Most people use 5–10 for raw and 3–5 for parsed samples.

---

## Development

```bash
# Install dev tools
pip install -e ".[dev]"

# Run type checker
mypy src

# Format + lint
ruff check --fix .
ruff format .

# Run tests (when you add them)
pytest
```

---

## Troubleshooting

**No data arriving**
- Confirm Data Out is **On** in FH6 HUD settings
- Verify the port in game matches `FORZA_UDP_PORT`
- Try `forza-telemetry status`
- Use `tcpdump -i any port 20066 -X` (or Wireshark) to verify packets are arriving

**Dashboard says "Collector not found"**
- Make sure the collector is running first
- Check that `/tmp/forza-telemetry.sock` exists
- When using Docker, both containers must share the `/tmp` volume

**High disk usage**
- Increase `RAW_STORAGE_INTERVAL` and/or `PARSED_STORAGE_INTERVAL`
- Add a TTL index on `raw_packets.ts` for automatic cleanup

---

## Companion Projects

This project can optionally integrate with the companion car database tool:

- **[fh6cardata](https://github.com/shikkie/fh6cardata)** — A separate Flask + React app that maintains a mapping of Forza car ordinals to real car information (year, manufacturer, model, etc.).  
  The live dashboard can query it to show friendly car names instead of just the raw ordinal ID.

See the `FH6CARDATA_API` setting in `.env.example` to enable it.

---

## License

MIT

---

## Acknowledgments

- Official packet documentation from [Forza Support](https://support.forza.net/hc/en-us/articles/51744149102611)
- The amazing [Textual](https://textual.textualize.io/) team
- All the reverse-engineering work done by the Forza modding community over the years

Enjoy your data!
