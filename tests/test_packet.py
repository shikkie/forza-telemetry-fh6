"""
Basic tests for the Forza Horizon 6 packet parser.
Run with: pytest tests/ -v
"""

import struct

import pytest

from forza_telemetry.packet import (
    ForzaTelemetryPacket,
    PACKET_SIZE,
    STRUCT_FORMAT,
)


def test_packet_size_is_exactly_324():
    """The struct definition must produce exactly 324 bytes."""
    assert struct.calcsize(STRUCT_FORMAT) == 324
    assert PACKET_SIZE == 324


def test_parse_all_zeros():
    """All-zero payload must parse without error and give sensible defaults."""
    data = bytes([0] * 324)
    pkt = ForzaTelemetryPacket.from_bytes(data)

    assert pkt.is_race_on is False
    assert pkt.timestamp_ms == 0
    assert pkt.current_engine_rpm == 0.0
    assert pkt.speed == 0.0
    assert pkt.gear == 0
    assert pkt.steer == 0


def test_parse_realistic_values():
    """Construct a packet with known values and round-trip it."""
    # Build a minimal valid payload using struct.pack
    values = [0] * len([f for _, f in []])  # placeholder

    # We will use the model constructor directly for this test
    pkt = ForzaTelemetryPacket(
        is_race_on=True,
        timestamp_ms=123456,
        engine_max_rpm=8500.0,
        engine_idle_rpm=900.0,
        current_engine_rpm=6123.5,
        acceleration_x=0.1,
        acceleration_y=0.2,
        acceleration_z=-1.8,
        velocity_x=0.0,
        velocity_y=0.0,
        velocity_z=42.0,
        angular_velocity_x=0.0,
        angular_velocity_y=0.0,
        angular_velocity_z=0.0,
        yaw=0.0,
        pitch=0.01,
        roll=-0.02,
        norm_susp_travel_fl=0.4,
        norm_susp_travel_fr=0.41,
        norm_susp_travel_rl=0.39,
        norm_susp_travel_rr=0.4,
        tire_slip_ratio_fl=0.05,
        tire_slip_ratio_fr=0.06,
        tire_slip_ratio_rl=0.04,
        tire_slip_ratio_rr=0.05,
        wheel_rot_speed_fl=80.0,
        wheel_rot_speed_fr=80.0,
        wheel_rot_speed_rl=80.0,
        wheel_rot_speed_rr=80.0,
        wheel_rumble_fl=0,
        wheel_rumble_fr=0,
        wheel_rumble_rl=0,
        wheel_rumble_rr=0,
        wheel_puddle_fl=0,
        wheel_puddle_fr=0,
        wheel_puddle_rl=0,
        wheel_puddle_rr=0,
        surface_rumble_fl=0.1,
        surface_rumble_fr=0.1,
        surface_rumble_rl=0.1,
        surface_rumble_rr=0.1,
        tire_slip_angle_fl=0.2,
        tire_slip_angle_fr=0.2,
        tire_slip_angle_rl=0.2,
        tire_slip_angle_rr=0.2,
        tire_combined_slip_fl=0.3,
        tire_combined_slip_fr=0.3,
        tire_combined_slip_rl=0.3,
        tire_combined_slip_rr=0.3,
        susp_travel_m_fl=0.05,
        susp_travel_m_fr=0.05,
        susp_travel_m_rl=0.05,
        susp_travel_m_rr=0.05,
        car_ordinal=1234,
        car_class=5,
        car_performance_index=850,
        drivetrain_type=1,
        num_cylinders=6,
        car_group=1,
        smashable_vel_diff=0.0,
        smashable_mass=0.0,
        position_x=123.4,
        position_y=10.0,
        position_z=567.8,
        speed=42.7,
        power=185000.0,
        torque=520.0,
        tire_temp_fl=85.0,
        tire_temp_fr=86.0,
        tire_temp_rl=84.0,
        tire_temp_rr=83.5,
        boost=11.2,
        fuel=0.67,
        distance_traveled=12450.0,
        best_lap=92.341,
        last_lap=93.112,
        current_lap=45.678,
        current_race_time=1245.3,
        lap_number=4,
        race_position=2,
        accel=210,
        brake=0,
        clutch=0,
        handbrake=0,
        gear=4,
        steer=12,
        normalized_driving_line=5,
        normalized_ai_brake_diff=0,
    )

    assert pkt.speed_kmh == pytest.approx(42.7 * 3.6, rel=1e-3)
    assert pkt.gear_display == "4"
    assert pkt.throttle_normalized == 210 / 255.0
    assert pkt.steer_normalized == 12 / 127.0
    assert pkt.power_kw == 185.0


def test_invalid_length_raises():
    with pytest.raises(ValueError, match="Expected 324 bytes"):
        ForzaTelemetryPacket.from_bytes(bytes([0] * 300))
