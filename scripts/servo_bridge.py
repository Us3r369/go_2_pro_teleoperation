"""Laptop-side bridge: head yaw (or a synthetic sweep) -> UDP -> ESP32 servo.

This is the intermediary that connects the VR headset's head rotation to the
camera pan servo. The robot is never touched here; this drives only the servo
via the ESP32 UDP receiver (``esp32/servo_udp``).

Two input sources
-----------------
* ``--source synthetic`` (default) -- a slow sine sweep generated locally. Use
  this FIRST to prove the laptop->ESP32->servo pipe end to end, independent of
  the head-pose math. The servo should track a smooth back-and-forth.
* ``--source headpose`` -- poll the running VR debug server's ``/state`` endpoint
  (``vr_joystick_debug.py``), read ``head_angles.yaw_deg``, and map it to a servo
  angle. The camera is currently 1-DOF, so only **yaw** (left/right) is used.

Transport
---------
Angles are sent as ASCII degrees in fire-and-forget UDP datagrams: it is a
continuous setpoint stream, so newest-wins and dropped packets are harmless.
The ESP32's IP is discovered automatically from its broadcast beacon -- this
process binds the hello port, learns the board's address from the first beacon,
and streams setpoints there. Pass ``--esp32-ip`` to skip discovery.

The ``yaw_to_servo_angle`` and ``synthetic_sweep_angle`` mappings are pure and
unit-tested in ``tests/test_servo_bridge.py``.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import ssl
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Pure logic (no I/O, no network) -- unit-tested in tests/test_servo_bridge.py
# ---------------------------------------------------------------------------

DEFAULT_SERVO_MIN = 0.0
DEFAULT_SERVO_MAX = 180.0
HELLO_PREFIX = "GO2SERVO"


def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


def yaw_to_servo_angle(
    yaw_deg: float,
    *,
    yaw_range_deg: float = 90.0,
    servo_min: float = DEFAULT_SERVO_MIN,
    servo_max: float = DEFAULT_SERVO_MAX,
    invert: bool = False,
) -> float:
    """Map head yaw (degrees) to a servo angle (degrees).

    Head looking straight ahead (``yaw_deg == 0``) maps to the servo centre.
    ``+/- yaw_range_deg`` maps to the servo's travel limits; yaw beyond that is
    clamped. ``invert`` flips the direction to match the servo's mounting.
    """
    if yaw_range_deg <= 0.0:
        raise ValueError("yaw_range_deg must be positive")
    norm = clamp(yaw_deg / yaw_range_deg, -1.0, 1.0)
    if invert:
        norm = -norm
    mid = (servo_min + servo_max) / 2.0
    half = (servo_max - servo_min) / 2.0
    return mid + norm * half


def synthetic_sweep_angle(
    t_s: float,
    *,
    period_s: float = 4.0,
    servo_min: float = DEFAULT_SERVO_MIN,
    servo_max: float = DEFAULT_SERVO_MAX,
) -> float:
    """A sine sweep between ``servo_min`` and ``servo_max``; pure in time ``t_s``.

    Used to validate the transport + firmware without involving the headset.
    Starts at the servo centre (``t_s == 0``) and never exceeds the limits.
    """
    if period_s <= 0.0:
        raise ValueError("period_s must be positive")
    mid = (servo_min + servo_max) / 2.0
    half = (servo_max - servo_min) / 2.0
    return mid + half * math.sin(2.0 * math.pi * t_s / period_s)


def encode_angle_packet(angle: float) -> bytes:
    """Encode a servo angle as the ASCII wire format the ESP32 parses."""
    return f"{angle:.2f}".encode("ascii")


def parse_hello(data: bytes) -> int | None:
    """Return the ESP32's listen port from a discovery beacon, else ``None``.

    The beacon is ``"GO2SERVO <port>"``; anything else returns ``None``.
    """
    try:
        parts = data.decode("ascii", "ignore").strip().split()
    except (UnicodeDecodeError, AttributeError):
        return None
    if len(parts) == 2 and parts[0] == HELLO_PREFIX:
        try:
            return int(parts[1])
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Transport (UDP send + zero-config discovery of the ESP32)
# ---------------------------------------------------------------------------


class ServoLink:
    """Send servo angles to the ESP32, learning its address from beacons.

    ``sock`` is a UDP socket bound to the hello port (non-blocking). Injecting it
    keeps this class testable with a fake socket. ``addr`` may be preset to skip
    discovery; otherwise ``try_discover`` fills it from the first valid beacon.
    """

    def __init__(self, sock: socket.socket, *, addr: tuple[str, int] | None = None):
        self._sock = sock
        self._addr = addr

    @property
    def addr(self) -> tuple[str, int] | None:
        return self._addr

    def try_discover(self) -> tuple[str, int] | None:
        """Drain pending beacons; adopt the latest valid sender. Non-blocking."""
        while True:
            try:
                data, sender = self._sock.recvfrom(256)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            port = parse_hello(data)
            if port is not None:
                self._addr = (sender[0], port)
        return self._addr

    def send_angle(self, angle: float) -> bool:
        """Send one angle setpoint. No-op (returns ``False``) until discovered."""
        if self._addr is None:
            return False
        self._sock.sendto(encode_angle_packet(angle), self._addr)
        return True


def make_socket(hello_port: int) -> socket.socket:
    """A non-blocking UDP socket bound to ``hello_port`` for beacons + sending."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", hello_port))
    sock.setblocking(False)
    return sock


def fetch_head_yaw(state_url: str, ctx: ssl.SSLContext) -> float | None:
    """Read ``head_angles.yaw_deg`` from the VR debug server, or ``None``."""
    with urllib.request.urlopen(state_url, timeout=1.0, context=ctx) as resp:
        data = json.load(resp)
    angles = data.get("head_angles")
    if not angles or "yaw_deg" not in angles:
        return None
    return float(angles["yaw_deg"])


# ---------------------------------------------------------------------------
# Run loops
# ---------------------------------------------------------------------------


def _period(rate_hz: float) -> float:
    return 1.0 / rate_hz if rate_hz > 0 else 0.0


def run_synthetic(link: ServoLink, args: argparse.Namespace) -> None:
    print(f"Synthetic sweep: {args.sweep_period:.1f}s period at {args.rate:.0f} Hz")
    period = _period(args.rate)
    start = time.monotonic()
    last_print = 0.0
    while True:
        now = time.monotonic()
        link.try_discover()
        angle = synthetic_sweep_angle(
            now - start,
            period_s=args.sweep_period,
            servo_min=args.servo_min,
            servo_max=args.servo_max,
        )
        sent = link.send_angle(angle)
        if now - last_print > 0.5:
            last_print = now
            where = link.addr or "discovering ESP32..."
            print(f"angle={angle:6.1f}  -> {where}  {'sent' if sent else '(waiting)'}")
        time.sleep(period)


def run_headpose(link: ServoLink, args: argparse.Namespace) -> None:
    print(f"Head-yaw source: polling {args.state_url} at {args.rate:.0f} Hz")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # VR server uses a self-signed cert
    period = _period(args.rate)
    last_print = 0.0
    while True:
        now = time.monotonic()
        link.try_discover()
        try:
            yaw = fetch_head_yaw(args.state_url, ctx)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            yaw = None
        sent = False
        angle = None
        if yaw is not None:
            angle = yaw_to_servo_angle(
                yaw,
                yaw_range_deg=args.yaw_range,
                servo_min=args.servo_min,
                servo_max=args.servo_max,
                invert=args.invert,
            )
            sent = link.send_angle(angle)
        if now - last_print > 0.5:
            last_print = now
            where = link.addr or "discovering ESP32..."
            if angle is None:
                print(f"yaw=  --   -> {where}  (no head data; is the Quest in VR?)")
            else:
                print(
                    f"yaw={yaw:+6.1f}  angle={angle:6.1f}  -> {where}  "
                    f"{'sent' if sent else '(waiting)'}"
                )
        time.sleep(period)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--source",
        choices=("synthetic", "headpose"),
        default="synthetic",
        help="angle source (default: synthetic sweep, for proving the pipe)",
    )
    p.add_argument("--rate", type=float, default=50.0, help="send rate in Hz")
    p.add_argument(
        "--state-url",
        default="https://localhost:8443/state",
        help="VR debug server /state endpoint (headpose source)",
    )
    p.add_argument(
        "--yaw-range",
        type=float,
        default=90.0,
        help="head yaw (deg) that maps to full servo travel",
    )
    p.add_argument("--invert", action="store_true", help="flip servo direction")
    p.add_argument("--servo-min", type=float, default=DEFAULT_SERVO_MIN)
    p.add_argument("--servo-max", type=float, default=DEFAULT_SERVO_MAX)
    p.add_argument(
        "--sweep-period", type=float, default=4.0, help="synthetic period (s)"
    )
    p.add_argument(
        "--hello-port", type=int, default=8889, help="UDP port for ESP32 beacons"
    )
    p.add_argument("--esp32-ip", default=None, help="ESP32 IP to skip auto-discovery")
    p.add_argument(
        "--esp32-port",
        type=int,
        default=8888,
        help="ESP32 listen port (with --esp32-ip)",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    sock = make_socket(args.hello_port)
    preset = (args.esp32_ip, args.esp32_port) if args.esp32_ip else None
    link = ServoLink(sock, addr=preset)
    if preset:
        print(f"Targeting ESP32 at {preset[0]}:{preset[1]} (discovery skipped)")
    else:
        print(f"Waiting for ESP32 beacon on UDP:{args.hello_port} ...")
    try:
        if args.source == "synthetic":
            run_synthetic(link, args)
        else:
            run_headpose(link, args)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
