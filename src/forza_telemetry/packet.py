"""
Forza Horizon 6 Telemetry Packet Parser (324 bytes)

Official packet structure from:
https://support.forza.net/hc/en-us/articles/51744149102611-Forza-Horizon-6-Data-Out-Documentation

This module provides a fast struct-based parser that produces a validated
Pydantic v2 model. The parser is designed to be called at high frequency
(60+ Hz) from the UDP receive path.
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# Exact struct format for the 324-byte FH6 packet.
# We use a list of (name, format) so we can generate clean error messages
# and easily maintain the spec.
# =============================================================================

# Format characters (little-endian)
# i = int32, I = uint32, f = float32, H = uint16, B = uint8, b = int8, x = pad
_PACKET_FIELDS: list[tuple[str, str]] = [
    ("is_race_on", "i"),
    ("timestamp_ms", "I"),
    # Engine
    ("engine_max_rpm", "f"),
    ("engine_idle_rpm", "f"),
    ("current_engine_rpm", "f"),
    # Local space acceleration (m/s^2), X=right, Y=up, Z=forward
    ("acceleration_x", "f"),
    ("acceleration_y", "f"),
    ("acceleration_z", "f"),
    # Local space velocity (m/s)
    ("velocity_x", "f"),
    ("velocity_y", "f"),
    ("velocity_z", "f"),
    # Angular velocity (rad/s) - pitch, yaw, roll
    ("angular_velocity_x", "f"),
    ("angular_velocity_y", "f"),
    ("angular_velocity_z", "f"),
    # Orientation (radians)
    ("yaw", "f"),
    ("pitch", "f"),
    ("roll", "f"),
    # Suspension (normalized 0.0 = max stretch, 1.0 = max compression)
    ("norm_susp_travel_fl", "f"),
    ("norm_susp_travel_fr", "f"),
    ("norm_susp_travel_rl", "f"),
    ("norm_susp_travel_rr", "f"),
    # Tire slip ratio (|ratio| > 1.0 = loss of grip)
    ("tire_slip_ratio_fl", "f"),
    ("tire_slip_ratio_fr", "f"),
    ("tire_slip_ratio_rl", "f"),
    ("tire_slip_ratio_rr", "f"),
    # Wheel rotation speed (rad/s)
    ("wheel_rot_speed_fl", "f"),
    ("wheel_rot_speed_fr", "f"),
    ("wheel_rot_speed_rl", "f"),
    ("wheel_rot_speed_rr", "f"),
    # Rumble strip contact (0/1)
    ("wheel_rumble_fl", "i"),
    ("wheel_rumble_fr", "i"),
    ("wheel_rumble_rl", "i"),
    ("wheel_rumble_rr", "i"),
    # In puddle (0/1)
    ("wheel_puddle_fl", "i"),
    ("wheel_puddle_fr", "i"),
    ("wheel_puddle_rl", "i"),
    ("wheel_puddle_rr", "i"),
    # Surface rumble (force feedback scalar)
    ("surface_rumble_fl", "f"),
    ("surface_rumble_fr", "f"),
    ("surface_rumble_rl", "f"),
    ("surface_rumble_rr", "f"),
    # Tire slip angle (|angle| > 1.0 = loss of grip)
    ("tire_slip_angle_fl", "f"),
    ("tire_slip_angle_fr", "f"),
    ("tire_slip_angle_rl", "f"),
    ("tire_slip_angle_rr", "f"),
    # Combined slip (|slip| > 1.0 = loss of grip)
    ("tire_combined_slip_fl", "f"),
    ("tire_combined_slip_fr", "f"),
    ("tire_combined_slip_rl", "f"),
    ("tire_combined_slip_rr", "f"),
    # Actual suspension travel (meters)
    ("susp_travel_m_fl", "f"),
    ("susp_travel_m_fr", "f"),
    ("susp_travel_m_rl", "f"),
    ("susp_travel_m_rr", "f"),
    # Car identity
    ("car_ordinal", "i"),
    ("car_class", "i"),
    ("car_performance_index", "i"),
    ("drivetrain_type", "i"),
    ("num_cylinders", "i"),
    # FH6-specific (not in Forza Motorsport dash format)
    ("car_group", "I"),
    ("smashable_vel_diff", "f"),
    ("smashable_mass", "f"),
    # World position (meters)
    ("position_x", "f"),
    ("position_y", "f"),
    ("position_z", "f"),
    # Vehicle dynamics
    ("speed", "f"),          # m/s
    ("power", "f"),          # Watts
    ("torque", "f"),         # Nm
    # Tire temperature (Celsius, presumably)
    ("tire_temp_fl", "f"),
    ("tire_temp_fr", "f"),
    ("tire_temp_rl", "f"),
    ("tire_temp_rr", "f"),
    # Boost (PSI above atmospheric)
    ("boost", "f"),
    # Fuel (0.0 = empty, 1.0 = full)
    ("fuel", "f"),
    ("distance_traveled", "f"),  # meters
    # Lap times in seconds (0.0 if not applicable)
    ("best_lap", "f"),
    ("last_lap", "f"),
    ("current_lap", "f"),
    ("current_race_time", "f"),  # seconds since driving started
    # Race progress
    ("lap_number", "H"),     # uint16
    ("race_position", "B"),  # uint8
    # Driver inputs (0-255)
    ("accel", "B"),
    ("brake", "B"),
    ("clutch", "B"),
    ("handbrake", "B"),
    # Gear (0=R, 1=1, ..., 10=10, 11=N?)
    ("gear", "B"),
    # Steering (-127 = full left, 0 = center, 127 = full right)
    ("steer", "b"),
    # Driving line / AI brake difference (-127..127)
    ("normalized_driving_line", "b"),
    ("normalized_ai_brake_diff", "b"),
    # One byte of padding / reserved to reach exactly 324 bytes
    ("_pad", "x"),
]

# Build the struct format string once at import time
_STRUCT_FORMAT: str = "<" + "".join(fmt for _, fmt in _PACKET_FIELDS)
_STRUCT_SIZE: int = struct.calcsize(_STRUCT_FORMAT)

# Sanity check at import
if _STRUCT_SIZE != 324:
    raise RuntimeError(
        f"CRITICAL: Computed packet size is {_STRUCT_SIZE} bytes, expected 324. "
        "Update _PACKET_FIELDS to match the official FH6 specification."
    )

# Public constant
PACKET_SIZE: int = 324
STRUCT_FORMAT: str = _STRUCT_FORMAT


# =============================================================================
# Pydantic v2 Model
# =============================================================================

class ForzaTelemetryPacket(BaseModel):
    """
    Strongly typed, validated model of a complete FH6 telemetry packet.

    All fields use descriptive names and correct Python types.
    The model is configured for high-speed construction from the UDP path.
    """

    model_config = ConfigDict(
        frozen=False,           # We may mutate a few derived fields
        validate_assignment=False,
        arbitrary_types_allowed=False,
        extra="ignore",
    )

    # --- Core state ---
    is_race_on: bool = Field(description="True when actively driving/racing")
    timestamp_ms: int = Field(description="Game monotonic timestamp in milliseconds (can wrap)")

    # --- Engine ---
    engine_max_rpm: float
    engine_idle_rpm: float
    current_engine_rpm: float

    # --- Kinematics (car local space) ---
    acceleration_x: float  # m/s², right
    acceleration_y: float  # m/s², up
    acceleration_z: float  # m/s², forward

    velocity_x: float
    velocity_y: float
    velocity_z: float

    angular_velocity_x: float  # rad/s
    angular_velocity_y: float
    angular_velocity_z: float

    # --- Orientation ---
    yaw: float    # radians
    pitch: float
    roll: float

    # --- Suspension & Tires ---
    norm_susp_travel_fl: float
    norm_susp_travel_fr: float
    norm_susp_travel_rl: float
    norm_susp_travel_rr: float

    tire_slip_ratio_fl: float
    tire_slip_ratio_fr: float
    tire_slip_ratio_rl: float
    tire_slip_ratio_rr: float

    wheel_rot_speed_fl: float
    wheel_rot_speed_fr: float
    wheel_rot_speed_rl: float
    wheel_rot_speed_rr: float

    wheel_rumble_fl: int
    wheel_rumble_fr: int
    wheel_rumble_rl: int
    wheel_rumble_rr: int

    wheel_puddle_fl: int
    wheel_puddle_fr: int
    wheel_puddle_rl: int
    wheel_puddle_rr: int

    surface_rumble_fl: float
    surface_rumble_fr: float
    surface_rumble_rl: float
    surface_rumble_rr: float

    tire_slip_angle_fl: float
    tire_slip_angle_fr: float
    tire_slip_angle_rl: float
    tire_slip_angle_rr: float

    tire_combined_slip_fl: float
    tire_combined_slip_fr: float
    tire_combined_slip_rl: float
    tire_combined_slip_rr: float

    susp_travel_m_fl: float
    susp_travel_m_fr: float
    susp_travel_m_rl: float
    susp_travel_m_rr: float

    # --- Car identity ---
    car_ordinal: int
    car_class: int
    car_performance_index: int
    drivetrain_type: int  # 0=FWD, 1=RWD, 2=AWD
    num_cylinders: int
    car_group: int

    # --- Collision physics (FH6 specific) ---
    smashable_vel_diff: float
    smashable_mass: float

    # --- World position ---
    position_x: float
    position_y: float
    position_z: float

    # --- Dynamics ---
    speed: float          # m/s
    power: float          # Watts
    torque: float         # Nm

    tire_temp_fl: float
    tire_temp_fr: float
    tire_temp_rl: float
    tire_temp_rr: float

    boost: float          # PSI gauge
    fuel: float           # 0.0 - 1.0
    distance_traveled: float  # meters

    # --- Lap / Race timing ---
    best_lap: float       # seconds
    last_lap: float
    current_lap: float
    current_race_time: float

    lap_number: int
    race_position: int

    # --- Controls ---
    accel: int            # 0-255
    brake: int
    clutch: int
    handbrake: int
    gear: int             # 0=R, 1-10 gears, 11=N?
    steer: int            # -127 (left) ... 0 ... 127 (right)

    normalized_driving_line: int
    normalized_ai_brake_diff: int

    # --- Derived / convenience (populated by parser) ---
    received_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Wall-clock time when packet was received by this collector",
    )

    # --- Validators ---
    @field_validator("is_race_on", mode="before")
    @classmethod
    def _coerce_is_race_on(cls, v: int) -> bool:
        return bool(v)

    @field_validator(
        "accel", "brake", "clutch", "handbrake", "gear", "race_position",
        "lap_number", "steer", "normalized_driving_line", "normalized_ai_brake_diff",
        mode="before"
    )
    @classmethod
    def _clamp_u8(cls, v: int) -> int:
        return max(0, min(255, int(v)))

    # --- Class methods ---
    @classmethod
    def from_bytes(cls, data: bytes, received_at: datetime | None = None) -> ForzaTelemetryPacket:
        """
        Parse a raw 324-byte UDP payload into a validated model instance.

        Raises:
            ValueError: if len(data) != 324 or struct unpacking fails.
        """
        if len(data) != PACKET_SIZE:
            raise ValueError(f"Expected {PACKET_SIZE} bytes, got {len(data)}")

        try:
            values = struct.unpack(STRUCT_FORMAT, data)
        except struct.error as exc:
            raise ValueError(f"Failed to unpack FH6 packet: {exc}") from exc

        # Build kwargs from the field list (skip the padding field)
        kwargs: dict[str, object] = {}
        for (name, _), value in zip(_PACKET_FIELDS, values):
            if name == "_pad":
                continue
            kwargs[name] = value

        if received_at is not None:
            kwargs["received_at"] = received_at

        # Construct with validation
        return cls(**kwargs)

    def to_dict_for_storage(self) -> dict:
        """Return a JSON-serializable dict suitable for MongoDB telemetry_samples."""
        d = self.model_dump(exclude={"received_at"})
        d["received_at"] = self.received_at.isoformat()
        # Convert bool for clarity
        d["is_race_on"] = bool(self.is_race_on)
        return d

    # --- Convenience properties ---
    @property
    def speed_kmh(self) -> float:
        """Speed in kilometers per hour (common display unit)."""
        return self.speed * 3.6

    @property
    def speed_mph(self) -> float:
        """Speed in miles per hour."""
        return self.speed * 2.2369362920544

    @property
    def power_kw(self) -> float:
        """Power in kilowatts."""
        return self.power / 1000.0

    @property
    def power_hp(self) -> float:
        """SAE / Imperial horsepower."""
        return self.power * 0.00134102185866

    @property
    def power_ps(self) -> float:
        """Metric horsepower (Pferdestärke)."""
        return self.power * 0.0013596216173

    @property
    def throttle_normalized(self) -> float:
        """Throttle position as 0.0 - 1.0."""
        return self.accel / 255.0

    @property
    def brake_normalized(self) -> float:
        """Brake position as 0.0 - 1.0."""
        return self.brake / 255.0

    @property
    def steer_normalized(self) -> float:
        """Steering as -1.0 (full left) to +1.0 (full right)."""
        return self.steer / 127.0

    @property
    def gear_display(self) -> str:
        """Human friendly gear string."""
        if self.gear == 0:
            return "R"
        if self.gear == 11:
            return "N"
        return str(self.gear)

    @property
    def is_moving(self) -> bool:
        return self.speed > 0.5

    def __repr__(self) -> str:  # type: ignore[override]
        return (
            f"ForzaTelemetryPacket(race={self.is_race_on}, "
            f"rpm={self.current_engine_rpm:.0f}, "
            f"gear={self.gear_display}, "
            f"speed={self.speed_kmh:.1f}km/h, "
            f"pos=({self.position_x:.0f},{self.position_z:.0f}))"
        )
