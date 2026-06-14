"""Hardware-free tests for the VR joystick debugger's pure mapping logic."""

import importlib.util
import math
from pathlib import Path

import pytest

# Load the script as a module without needing a package on sys.path.
_SPEC = importlib.util.spec_from_file_location(
    "vr_joystick_debug",
    Path(__file__).resolve().parent.parent / "scripts" / "vr_joystick_debug.py",
)
vr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vr)


# --- apply_deadzone --------------------------------------------------------


def test_deadzone_zeros_small_input():
    assert vr.apply_deadzone(0.05, 0.1) == 0.0
    assert vr.apply_deadzone(-0.1, 0.1) == 0.0  # exactly at the edge -> 0


def test_deadzone_full_input_unchanged():
    assert vr.apply_deadzone(1.0, 0.1) == pytest.approx(1.0)
    assert vr.apply_deadzone(-1.0, 0.1) == pytest.approx(-1.0)


def test_deadzone_rescales_from_edge():
    # Just outside a 0.1 deadzone the output starts near 0 (no jump), not 0.2.
    assert vr.apply_deadzone(0.2, 0.1) == pytest.approx((0.2 - 0.1) / 0.9)


def test_deadzone_is_sign_symmetric():
    assert vr.apply_deadzone(0.5, 0.1) == pytest.approx(-vr.apply_deadzone(-0.5, 0.1))


def test_deadzone_rejects_bad_range():
    with pytest.raises(ValueError):
        vr.apply_deadzone(0.5, 1.0)
    with pytest.raises(ValueError):
        vr.apply_deadzone(0.5, -0.1)


# --- axes_to_velocity ------------------------------------------------------


def test_neutral_sticks_give_zero_velocity():
    assert vr.axes_to_velocity(0.0, 0.0, 0.0) == {"x": 0.0, "y": 0.0, "z": 0.0}


def test_stick_up_drives_forward():
    # WebXR reports stick-up as negative Y -> should be +x (forward) at max.
    v = vr.axes_to_velocity(0.0, -1.0, 0.0, max_linear=0.6)
    assert v["x"] == pytest.approx(0.6)
    assert v["y"] == 0.0 and v["z"] == 0.0


def test_stick_left_strafes_left():
    v = vr.axes_to_velocity(-1.0, 0.0, 0.0, max_linear=0.6)
    assert v["y"] == pytest.approx(0.6)


def test_right_stick_left_turns_left():
    v = vr.axes_to_velocity(0.0, 0.0, -1.0, max_angular=0.8)
    assert v["z"] == pytest.approx(0.8)


def test_velocity_is_clamped_to_limits():
    # Deadzone rescaling can't push magnitude past 1, so output stays within limits.
    v = vr.axes_to_velocity(-1.0, -1.0, -1.0, max_linear=0.6, max_angular=0.8)
    assert abs(v["x"]) <= 0.6 and abs(v["y"]) <= 0.6 and abs(v["z"]) <= 0.8


def test_deadzone_applied_inside_mapping():
    assert vr.axes_to_velocity(0.05, 0.05, 0.05, deadzone=0.1) == {
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
    }


# --- velocity_from_controllers ---------------------------------------------


def test_picks_thumbstick_axes_by_handedness():
    controllers = [
        {"handedness": "left", "axes": [0.0, 0.0, 0.0, -1.0]},  # left stick up
        {"handedness": "right", "axes": [0.0, 0.0, -1.0, 0.0]},  # right stick left
    ]
    v = vr.velocity_from_controllers(controllers)
    assert v["x"] == pytest.approx(0.6)  # forward from left stick
    assert v["z"] == pytest.approx(0.8)  # yaw-left from right stick


def test_missing_controllers_degrade_to_zero():
    assert vr.velocity_from_controllers([]) == {"x": 0.0, "y": 0.0, "z": 0.0}


def test_short_axes_array_does_not_crash():
    # A controller reporting fewer than 4 axes must not raise.
    v = vr.velocity_from_controllers([{"handedness": "left", "axes": [0.0, 0.0]}])
    assert v == {"x": 0.0, "y": 0.0, "z": 0.0}


# --- update_state / read_state round-trip ----------------------------------


def test_update_state_computes_and_stores_velocity():
    payload = {
        "t": 1234,
        "controllers": [{"handedness": "left", "axes": [0.0, 0.0, 0.0, -1.0]}],
    }
    state = vr.update_state(payload)
    assert state["ts"] == 1234
    assert state["velocity"]["x"] == pytest.approx(0.6)
    assert vr.read_state()["ts"] == 1234


def test_uses_xr_standard_axis_indices():
    # Guard against regressing to the touchpad axes[0]/[1] bug.
    assert (vr.THUMBSTICK_X_AXIS, vr.THUMBSTICK_Y_AXIS) == (2, 3)


def test_neutral_velocity_magnitude_is_zero():
    v = vr.axes_to_velocity(0.0, 0.0, 0.0)
    assert math.hypot(v["x"], v["y"], v["z"]) == 0.0


# --- head pose: quaternion -> yaw/pitch ------------------------------------


def _quat_about_y(deg):
    half = math.radians(deg) / 2
    return (0.0, math.sin(half), 0.0, math.cos(half))


def _quat_about_x(deg):
    half = math.radians(deg) / 2
    return (math.sin(half), 0.0, 0.0, math.cos(half))


def test_identity_quaternion_is_zero_yaw_and_pitch():
    assert vr.quaternion_to_yaw(0.0, 0.0, 0.0, 1.0) == pytest.approx(0.0)
    assert vr.quaternion_to_pitch(0.0, 0.0, 0.0, 1.0) == pytest.approx(0.0)


def test_yaw_positive_is_turn_left():
    # +90 deg about the up (Y) axis should read as +90 deg (turn left).
    assert math.degrees(vr.quaternion_to_yaw(*_quat_about_y(90))) == pytest.approx(90.0)
    assert math.degrees(vr.quaternion_to_yaw(*_quat_about_y(-90))) == pytest.approx(
        -90.0
    )


def test_pitch_positive_is_looking_up():
    # +45 deg about X tilts the forward vector up -> +45 deg pitch.
    assert math.degrees(vr.quaternion_to_pitch(*_quat_about_x(45))) == pytest.approx(
        45.0
    )
    assert math.degrees(vr.quaternion_to_pitch(*_quat_about_x(-45))) == pytest.approx(
        -45.0
    )


def test_head_angles_deg_from_payload():
    x, y, z, w = _quat_about_y(30)
    head = {
        "position": {"x": 0, "y": 1.6, "z": 0},
        "orientation": {"x": x, "y": y, "z": z, "w": w},
    }
    angles = vr.head_angles_deg(head)
    assert angles["yaw_deg"] == pytest.approx(30.0)
    assert angles["pitch_deg"] == pytest.approx(0.0)


def test_head_angles_deg_none_when_absent():
    assert vr.head_angles_deg(None) is None


def test_head_angles_deg_defaults_to_identity():
    # Missing orientation must not crash; treat as looking straight ahead.
    angles = vr.head_angles_deg({"position": {"x": 0, "y": 0, "z": 0}})
    assert angles == {"yaw_deg": pytest.approx(0.0), "pitch_deg": pytest.approx(0.0)}


def test_position_magnitude():
    assert vr.position_magnitude({"x": 3.0, "y": 0.0, "z": 4.0}) == pytest.approx(5.0)
    assert vr.position_magnitude(None) == 0.0


# --- SessionLog: tee + end-of-run summary ----------------------------------


def test_session_log_tees_and_summarizes(tmp_path):
    path = tmp_path / "session.log"
    log = vr.SessionLog(path)
    log.line("header line")
    log.record(
        "[xr] sample",
        {
            "velocity": {"x": 0.5, "y": 0.1, "z": 0.2},
            "head": {"position": {"x": 0.0, "y": 0.0, "z": 3.0}},
        },
    )
    log.close()

    text = path.read_text()
    assert "header line" in text  # plain lines are written
    assert "[xr] sample" in text  # per-frame samples are written
    assert "Session summary" in text  # footer appended on close
    assert "input samples: 1" in text
    assert "x=0.500" in text  # peak velocity folded in
    assert "3.00 m" in text  # peak head travel folded in


def test_update_state_includes_head_angles():
    x, y, z, w = _quat_about_y(90)
    payload = {
        "t": 7,
        "controllers": [],
        "head": {
            "position": {"x": 0, "y": 1.6, "z": 0},
            "orientation": {"x": x, "y": y, "z": z, "w": w},
        },
    }
    state = vr.update_state(payload)
    assert state["head_angles"]["yaw_deg"] == pytest.approx(90.0)
    assert vr.read_state()["head"]["position"]["y"] == 1.6
