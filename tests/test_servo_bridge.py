"""Hardware-free unit tests for the head-yaw -> servo UDP bridge.

These cover the pure mapping logic and the transport's discovery/send behaviour
with a fake socket -- no ESP32, no network, no headset required.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load scripts/servo_bridge.py directly (the repo ships scripts, not a package).
_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "servo_bridge.py"
_spec = importlib.util.spec_from_file_location("servo_bridge", _MODULE_PATH)
servo_bridge = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(servo_bridge)


# --- yaw_to_servo_angle ----------------------------------------------------


def test_yaw_centre_maps_to_servo_centre():
    assert servo_bridge.yaw_to_servo_angle(0.0) == pytest.approx(90.0)


def test_yaw_full_range_maps_to_travel_limits():
    assert servo_bridge.yaw_to_servo_angle(90.0, yaw_range_deg=90.0) == pytest.approx(
        180.0
    )
    assert servo_bridge.yaw_to_servo_angle(-90.0, yaw_range_deg=90.0) == pytest.approx(
        0.0
    )


def test_yaw_beyond_range_is_clamped_to_limits():
    assert servo_bridge.yaw_to_servo_angle(200.0, yaw_range_deg=90.0) == pytest.approx(
        180.0
    )
    assert servo_bridge.yaw_to_servo_angle(-200.0, yaw_range_deg=90.0) == pytest.approx(
        0.0
    )


def test_invert_flips_direction():
    normal = servo_bridge.yaw_to_servo_angle(45.0, yaw_range_deg=90.0)
    inverted = servo_bridge.yaw_to_servo_angle(45.0, yaw_range_deg=90.0, invert=True)
    assert normal == pytest.approx(135.0)
    assert inverted == pytest.approx(45.0)


def test_custom_servo_span_respected():
    # A narrower servo span still centres at its own midpoint.
    mid = servo_bridge.yaw_to_servo_angle(0.0, servo_min=60.0, servo_max=120.0)
    hi = servo_bridge.yaw_to_servo_angle(90.0, servo_min=60.0, servo_max=120.0)
    assert mid == pytest.approx(90.0)
    assert hi == pytest.approx(120.0)


def test_non_positive_yaw_range_rejected():
    with pytest.raises(ValueError):
        servo_bridge.yaw_to_servo_angle(10.0, yaw_range_deg=0.0)


# --- synthetic_sweep_angle -------------------------------------------------


def test_sweep_starts_centred():
    assert servo_bridge.synthetic_sweep_angle(0.0) == pytest.approx(90.0)


def test_sweep_hits_extremes_at_quarter_periods():
    period = 4.0
    assert servo_bridge.synthetic_sweep_angle(
        period / 4, period_s=period
    ) == pytest.approx(180.0)
    assert servo_bridge.synthetic_sweep_angle(
        3 * period / 4, period_s=period
    ) == pytest.approx(0.0)


def test_sweep_stays_within_limits():
    for i in range(200):
        a = servo_bridge.synthetic_sweep_angle(i * 0.05, period_s=4.0)
        assert 0.0 <= a <= 180.0


def test_sweep_non_positive_period_rejected():
    with pytest.raises(ValueError):
        servo_bridge.synthetic_sweep_angle(1.0, period_s=0.0)


# --- wire format -----------------------------------------------------------


def test_encode_angle_packet_is_ascii_two_decimals():
    assert servo_bridge.encode_angle_packet(97.5) == b"97.50"
    assert servo_bridge.encode_angle_packet(0.0) == b"0.00"


def test_parse_hello_valid():
    assert servo_bridge.parse_hello(b"GO2SERVO 8888") == 8888


@pytest.mark.parametrize(
    "data",
    [b"", b"GO2SERVO", b"GO2SERVO notaport", b"WRONG 8888", b"GO2SERVO 8888 extra"],
)
def test_parse_hello_rejects_malformed(data):
    assert servo_bridge.parse_hello(data) is None


# --- ServoLink transport ---------------------------------------------------


class FakeSock:
    """Records sendto() calls and replays queued recvfrom() datagrams."""

    def __init__(self, incoming: list[tuple[bytes, tuple[str, int]]] | None = None):
        self.incoming = list(incoming or [])
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def recvfrom(self, _bufsize: int):
        if self.incoming:
            return self.incoming.pop(0)
        raise BlockingIOError

    def sendto(self, data: bytes, addr: tuple[str, int]):
        self.sent.append((data, addr))


def test_send_is_noop_before_discovery():
    sock = FakeSock()
    link = servo_bridge.ServoLink(sock)
    assert link.send_angle(123.0) is False
    assert sock.sent == []


def test_discovery_learns_addr_then_sends_there():
    sock = FakeSock(incoming=[(b"GO2SERVO 8888", ("192.168.1.50", 8889))])
    link = servo_bridge.ServoLink(sock)
    assert link.try_discover() == ("192.168.1.50", 8888)
    assert link.send_angle(90.0) is True
    assert sock.sent == [(b"90.00", ("192.168.1.50", 8888))]


def test_discovery_ignores_non_hello_traffic():
    sock = FakeSock(incoming=[(b"junk", ("10.0.0.9", 9999))])
    link = servo_bridge.ServoLink(sock)
    assert link.try_discover() is None
    assert link.send_angle(45.0) is False


def test_preset_addr_skips_discovery():
    sock = FakeSock()
    link = servo_bridge.ServoLink(sock, addr=("10.0.0.7", 8888))
    assert link.send_angle(12.34) is True
    assert sock.sent == [(b"12.34", ("10.0.0.7", 8888))]
