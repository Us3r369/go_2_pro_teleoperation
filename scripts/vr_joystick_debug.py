"""WebXR joystick + head-pose debugger for the Meta Quest 3 / 3S.

Purpose
-------
A *hardware-free* tool (it never touches the robot) to prove out the inputs that
VR teleoperation of the Go2 needs from the browser: the controllers' **thumbstick
positions** (the historic blocker), plus each frame's **headset 6DoF pose** (and
the hands' grip poses) -- the continuous "VR head-pose" signal the project's
future direction calls for. Every session is also saved to a logfile under
``logs/`` so you don't have to copy the terminal output by hand.

Why past attempts failed (read this first)
------------------------------------------
1. **WebXR needs a secure context.** ``navigator.xr`` is *undefined* on plain
   ``http://``. The Quest browser therefore captured nothing when pointed at an
   ``http://<laptop-ip>:8080`` page. This server speaks **HTTPS** (self-signed
   cert) so ``navigator.xr`` actually exists. You must accept the cert warning on
   the Quest the first time ("Advanced -> Proceed").
2. **Wrong axis indices.** In the WebXR ``xr-standard`` gamepad mapping the
   thumbstick is ``axes[2]`` (X) and ``axes[3]`` (Y). ``axes[0]/[1]`` are the
   (absent) touchpad and read a constant ``0,0`` -- the classic "joystick stuck at
   zero" bug.
3. **Wrong read path.** Controller gamepads are only populated for input sources
   of an **immersive** session, read **inside the XR frame loop** -- not via the
   plain ``navigator.getGamepads()`` API on a 2D page.

How to use
----------
1. Run this on the laptop (the Quest and laptop must be on the same network --
   e.g. both joined to the robot's AP, or the laptop's hotspot)::

       python scripts/vr_joystick_debug.py

   It prints the HTTPS URL to open.
2. On the **Quest**, open ``https://<laptop-ip>:8443/`` in the Meta Quest Browser,
   accept the self-signed cert warning, then tap **Enter VR** and move the sticks.
   (Inside immersive-VR you can't see a 2D web page, so the background brightens
   when you push a stick -- that's your in-headset "it's reading" confirmation.)
3. On the **laptop**, open ``https://localhost:8443/monitor`` to watch the live
   axis/button values, the head position + look-direction compass, *and* the
   velocity setpoint they would map to.
4. Stop with Ctrl-C. The full session (every sample + a peak-values summary) is
   written to ``logs/vr_session_<timestamp>.log``.

The ``axes_to_velocity`` and ``quaternion_to_yaw`` mappings here are the bridge to
the project's future direction: they are exactly the continuous-input adapters
(thumbstick -> velocity setpoint; head yaw -> heading) a real teleop core would
use, but they only *preview* the command -- no robot is connected.
"""

from __future__ import annotations

import json
import math
import socket
import ssl
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# Pure logic (no I/O, no network) -- unit-tested in tests/test_vr_joystick_debug.py
# ---------------------------------------------------------------------------

# Thumbstick axis indices in the WebXR "xr-standard" gamepad mapping.
THUMBSTICK_X_AXIS = 2
THUMBSTICK_Y_AXIS = 3


def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


def apply_deadzone(value: float, deadzone: float) -> float:
    """Zero out small jitter and rescale the remainder back to full range.

    Below ``deadzone`` (in magnitude) the output is ``0.0``. Just outside it the
    output starts at ``0.0`` again and ramps to ``+/-1.0`` at the stick extreme,
    so there is no discontinuous jump at the edge of the deadzone.
    """
    if deadzone < 0.0 or deadzone >= 1.0:
        raise ValueError("deadzone must be in [0.0, 1.0)")
    magnitude = abs(value)
    if magnitude <= deadzone:
        return 0.0
    sign = 1.0 if value > 0 else -1.0
    return sign * (magnitude - deadzone) / (1.0 - deadzone)


def axes_to_velocity(
    left_x: float,
    left_y: float,
    right_x: float,
    *,
    deadzone: float = 0.1,
    max_linear: float = 0.6,
    max_angular: float = 0.8,
) -> dict[str, float]:
    """Map raw thumbstick axes to a Go2 velocity setpoint ``{x, y, z}``.

    Conventions (matching the driver's ``Move`` command and ``remote_control``):

    * ``x`` -- forward/back, +forward. Left stick **up** drives forward. WebXR
      reports stick-up as a *negative* Y, hence the sign flip.
    * ``y`` -- strafe, +left. Left stick **left** strafes left (negative X -> +y).
    * ``z`` -- yaw, +left. Right stick **left** turns left (negative X -> +z).

    This is a *preview* of the command -- it does not send anything to a robot.
    """
    lx = apply_deadzone(left_x, deadzone)
    ly = apply_deadzone(left_y, deadzone)
    rx = apply_deadzone(right_x, deadzone)
    return {
        "x": clamp(-ly * max_linear, -max_linear, max_linear),
        "y": clamp(-lx * max_linear, -max_linear, max_linear),
        "z": clamp(-rx * max_angular, -max_angular, max_angular),
    }


def velocity_from_controllers(controllers: list[dict]) -> dict[str, float]:
    """Pick the left/right thumbstick axes out of a posted controller list.

    ``controllers`` is the JSON the browser posts: a list of
    ``{"handedness": "left"|"right", "axes": [...], "buttons": [...]}``. Missing
    controllers or short axis arrays degrade gracefully to ``0.0``.
    """

    def axis(hand: str, index: int) -> float:
        for c in controllers:
            if c.get("handedness") == hand:
                axes = c.get("axes") or []
                if index < len(axes):
                    return float(axes[index])
        return 0.0

    return axes_to_velocity(
        left_x=axis("left", THUMBSTICK_X_AXIS),
        left_y=axis("left", THUMBSTICK_Y_AXIS),
        right_x=axis("right", THUMBSTICK_X_AXIS),
    )


# --- Head pose -------------------------------------------------------------
#
# WebXR uses a right-handed, Y-up space whose default forward is -Z. The headset
# pose arrives as a position (metres) plus an orientation quaternion (x, y, z, w).


def quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    """Heading about the vertical (Y) axis, in radians.

    Yaw ``0`` faces -Z (the default forward); **positive yaw turns left**
    (counter-clockwise viewed from above), matching the robot's ``+z`` = turn-left
    convention. So head yaw drops straight onto angular velocity later.
    """
    return math.atan2(2.0 * (x * z + w * y), 1.0 - 2.0 * (x * x + y * y))


def quaternion_to_pitch(x: float, y: float, z: float, w: float) -> float:
    """Elevation of the forward (-Z) vector, in radians; **+up, -down**."""
    forward_y = 2.0 * (w * x - y * z)
    return math.asin(clamp(forward_y, -1.0, 1.0))


def head_angles_deg(head: dict | None) -> dict | None:
    """Yaw/pitch (degrees) of a posted head pose, or ``None`` if absent.

    ``head`` is ``{"position": {...}, "orientation": {"x","y","z","w"}}``. A
    missing/short orientation degrades to the identity (looking straight ahead).
    """
    if not head:
        return None
    q = head.get("orientation") or {}
    x = float(q.get("x", 0.0))
    y = float(q.get("y", 0.0))
    z = float(q.get("z", 0.0))
    w = float(q.get("w", 1.0))
    return {
        "yaw_deg": math.degrees(quaternion_to_yaw(x, y, z, w)),
        "pitch_deg": math.degrees(quaternion_to_pitch(x, y, z, w)),
    }


def position_magnitude(position: dict | None) -> float:
    """Distance (metres) of a ``{x, y, z}`` position from the origin."""
    if not position:
        return 0.0
    return math.sqrt(
        float(position.get("x", 0.0)) ** 2
        + float(position.get("y", 0.0)) ** 2
        + float(position.get("z", 0.0)) ** 2
    )


# ---------------------------------------------------------------------------
# Shared state (latest input posted by the headset)
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_latest_state: dict = {
    "controllers": [],
    "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
    "head": None,
    "head_angles": None,
    "ts": 0,
}


def update_state(payload: dict) -> dict:
    """Store the newest posted snapshot (controllers + head) with derived values."""
    controllers = payload.get("controllers", [])
    head = payload.get("head")
    state = {
        "controllers": controllers,
        "velocity": velocity_from_controllers(controllers),
        "head": head,
        "head_angles": head_angles_deg(head),
        "ts": payload.get("t", 0),
    }
    with _state_lock:
        _latest_state.clear()
        _latest_state.update(state)
    return state


def read_state() -> dict:
    with _state_lock:
        return dict(_latest_state)


# ---------------------------------------------------------------------------
# Session logging (tee console -> timestamped file, plus an end-of-run summary)
# ---------------------------------------------------------------------------


class SessionLog:
    """Mirror every printed line to a logfile and accumulate session stats.

    Writes are line-buffered and guarded by a lock, so the worker threads of the
    threaded HTTP server can all log safely. ``close()`` appends a summary so you
    get peak speeds / head travel without scrubbing the raw stream.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file = open(path, "a", buffering=1, encoding="utf-8")
        self._lock = threading.Lock()
        self._start = datetime.now()
        self._samples = 0
        self._peak = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._peak_head_m = 0.0

    @property
    def path(self) -> Path:
        return self._path

    def line(self, message: str) -> None:
        """Print to the console and append to the logfile."""
        with self._lock:
            print(message, flush=True)
            self._file.write(message + "\n")

    def record(self, message: str, state: dict) -> None:
        """Tee a per-frame line and fold its values into the running summary."""
        with self._lock:
            print(message, flush=True)
            self._file.write(message + "\n")
            self._samples += 1
            v = state.get("velocity") or {}
            for axis in ("x", "y", "z"):
                self._peak[axis] = max(self._peak[axis], abs(float(v.get(axis, 0.0))))
            head = state.get("head")
            if head:
                self._peak_head_m = max(
                    self._peak_head_m, position_magnitude(head.get("position"))
                )

    def close(self) -> None:
        with self._lock:
            duration = (datetime.now() - self._start).total_seconds()
            summary = [
                "-" * 64,
                "Session summary",
                f"  duration:      {duration:.1f} s",
                f"  input samples: {self._samples}",
                f"  peak |vel|:    x={self._peak['x']:.3f} "
                f"y={self._peak['y']:.3f} z={self._peak['z']:.3f}",
                f"  peak head travel from origin: {self._peak_head_m:.2f} m",
                "-" * 64,
            ]
            text = "\n".join(summary)
            print(text, flush=True)
            self._file.write(text + "\n")
            self._file.close()
        print(f"Log written to {self._path}")


# Set in main(); the threaded request handlers tee through this.
_session_log: SessionLog | None = None


# ---------------------------------------------------------------------------
# Web pages
# ---------------------------------------------------------------------------

# Page opened ON THE QUEST. Diagnoses WebXR support, enters immersive-VR, reads
# thumbsticks inside the XR frame loop, and streams them to the laptop.
CAPTURE_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Go2 VR Joystick Debug</title>
<style>
  body { font-family: system-ui, Arial, sans-serif; background:#10131a; color:#e6e8ec;
         margin:0; padding:24px; }
  h1 { font-size:22px; }
  #enter { font-size:24px; padding:18px 36px; margin:16px 0; border:none; border-radius:10px;
           background:#1a73e8; color:#fff; cursor:pointer; }
  #enter:disabled { background:#444; cursor:not-allowed; }
  .row { margin:6px 0; }
  .ok { color:#34a853; } .bad { color:#ea4335; }
  pre { background:#0a0c11; padding:14px; border-radius:8px; overflow:auto; font-size:14px;
        line-height:1.5; }
  #log { height:180px; }
</style>
</head>
<body>
<h1>Go2 VR Joystick Debug &mdash; capture page</h1>
<p>Open this <b>on the Quest</b>. Check the diagnostics below are all green, tap
<b>Enter VR</b>, and move the thumbsticks. Watch the live values on your laptop at
<code>/monitor</code>. In the headset the background brightens when a stick moves.</p>

<button id="enter" disabled>Enter VR</button>

<div id="diag"></div>
<h3>Live input (also visible here on a desktop browser)</h3>
<pre id="live">no data yet</pre>
<h3>Log</h3>
<pre id="log"></pre>

<script>
const logEl = document.getElementById('log');
const liveEl = document.getElementById('live');
const diagEl = document.getElementById('diag');
const enterBtn = document.getElementById('enter');

function log(msg) {
  console.log(msg);
  logEl.textContent += msg + "\\n";
  logEl.scrollTop = logEl.scrollHeight;
}
function row(label, ok, detail) {
  return `<div class="row">${label}: <span class="${ok ? 'ok' : 'bad'}">` +
         `${ok ? 'YES' : 'NO'}</span>${detail ? ' &mdash; ' + detail : ''}</div>`;
}

let xrSession = null, gl = null, refSpace = null, lastPost = 0;

async function checkSupport() {
  let html = '';
  const secure = window.isSecureContext;
  html += row('Secure context (HTTPS)', secure,
              secure ? '' : 'WebXR is disabled on http:// &mdash; use the https:// URL');
  const hasXR = 'xr' in navigator && !!navigator.xr;
  html += row('navigator.xr present', hasXR,
              hasXR ? '' : 'no WebXR in this browser/context');
  let vrSupported = false;
  if (hasXR) {
    try { vrSupported = await navigator.xr.isSessionSupported('immersive-vr'); }
    catch (e) { log('isSessionSupported error: ' + e); }
  }
  html += row('immersive-vr supported', vrSupported);
  diagEl.innerHTML = html;
  enterBtn.disabled = !(secure && hasXR && vrSupported);
  if (enterBtn.disabled) log('Enter VR disabled: a check above failed.');
}

async function enterVR() {
  try {
    log('Requesting immersive-vr session...');
    const session = await navigator.xr.requestSession('immersive-vr');
    xrSession = session;
    const canvas = document.createElement('canvas');
    gl = canvas.getContext('webgl', { xrCompatible: true, antialias: false });
    await gl.makeXRCompatible();
    session.updateRenderState({ baseLayer: new XRWebGLLayer(session, gl) });
    // 'local-floor' puts the origin on the floor (so head pos.y is a real height).
    // Fall back to 'local' if the headset/space doesn't support it.
    try {
      refSpace = await session.requestReferenceSpace('local-floor');
      log('Reference space: local-floor (y = height above floor).');
    } catch (e) {
      refSpace = await session.requestReferenceSpace('local');
      log('Reference space: local (local-floor unavailable).');
    }
    session.addEventListener('end', () => {
      log('Session ended.');
      xrSession = null;
    });
    session.requestAnimationFrame(onXRFrame);
    log('In VR. Move the thumbsticks and your head.');
  } catch (e) {
    log('requestSession failed: ' + e);
  }
}

function round3(n) { return Math.round(n * 1000) / 1000; }

function xform(pose) {
  // Compact {position, orientation} from an XRPose's rigid transform.
  const p = pose.transform.position, o = pose.transform.orientation;
  return {
    position: { x: round3(p.x), y: round3(p.y), z: round3(p.z) },
    orientation: { x: round3(o.x), y: round3(o.y), z: round3(o.z), w: round3(o.w) },
  };
}

function readControllers(session, frame) {
  const out = [];
  for (const src of session.inputSources) {
    if (!src.gamepad) continue;
    const entry = {
      handedness: src.handedness,
      axes: Array.from(src.gamepad.axes),
      buttons: src.gamepad.buttons.map(b => ({
        pressed: b.pressed, touched: b.touched,
        value: Math.round(b.value * 100) / 100,
      })),
    };
    // Where the hand physically is (grip pose), if tracking has it this frame.
    if (src.gripSpace) {
      const gp = frame.getPose(src.gripSpace, refSpace);
      if (gp) entry.grip = xform(gp);
    }
    out.push(entry);
  }
  return out;
}

function onXRFrame(time, frame) {
  const session = frame.session;
  session.requestAnimationFrame(onXRFrame);

  const glLayer = session.renderState.baseLayer;
  gl.bindFramebuffer(gl.FRAMEBUFFER, glLayer.framebuffer);

  const controllers = readControllers(session, frame);

  // Headset pose (6DoF). getViewerPose can return null briefly while tracking
  // settles, so guard it.
  const viewerPose = frame.getViewerPose(refSpace);
  const head = viewerPose ? xform(viewerPose) : null;

  // In-headset feedback: background brightens with thumbstick magnitude so you
  // can confirm capture works without needing to see a 2D page.
  let mag = 0;
  for (const c of controllers) {
    const x = c.axes[2] || 0, y = c.axes[3] || 0;
    mag = Math.max(mag, Math.hypot(x, y));
  }
  gl.clearColor(0.04 + 0.6 * Math.min(mag, 1), 0.05, 0.12, 1.0);
  gl.clear(gl.COLOR_BUFFER_BIT);

  liveEl.textContent = JSON.stringify({ head, controllers }, null, 2);

  if (time - lastPost > 66) {  // ~15 Hz
    lastPost = time;
    fetch('/xr_input', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ head, controllers, t: Math.round(time) }),
      keepalive: true,
    }).catch(e => log('post failed: ' + e));
  }
}

enterBtn.addEventListener('click', enterVR);
checkSupport();
</script>
</body>
</html>
"""

# Page opened ON THE LAPTOP. Polls /state and renders the live values, since the
# Quest is inside immersive-VR and can't show a 2D page.
MONITOR_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Go2 VR Joystick Monitor</title>
<style>
  body { font-family: system-ui, Arial, sans-serif; background:#10131a; color:#e6e8ec;
         margin:0; padding:24px; }
  h1 { font-size:22px; } h2 { font-size:18px; margin-top:24px; }
  .vel span { display:inline-block; min-width:120px; }
  .bar { height:18px; background:#0a0c11; border-radius:4px; position:relative; width:260px;
         display:inline-block; vertical-align:middle; }
  .bar > i { position:absolute; top:0; bottom:0; left:50%; width:2px; background:#1a73e8; }
  pre { background:#0a0c11; padding:14px; border-radius:8px; overflow:auto; }
  .stale { color:#ea4335; }
  .head { display:flex; gap:32px; align-items:center; flex-wrap:wrap; }
  .head .vals span { display:inline-block; min-width:150px; }
  .compass { width:120px; height:120px; border-radius:50%; background:#0a0c11;
             position:relative; border:1px solid #2a2f3a; }
  .compass::after { content:'N'; position:absolute; top:4px; left:50%;
                    transform:translateX(-50%); font-size:12px; color:#5f6368; }
  .compass > .arrow { position:absolute; top:50%; left:50%; width:0; height:0;
    border-left:8px solid transparent; border-right:8px solid transparent;
    border-bottom:48px solid #1a73e8; transform-origin:50% 100%;
    transform:translate(-50%,-100%) rotate(0deg); }
</style>
</head>
<body>
<h1>Go2 VR Joystick + Head Monitor</h1>
<p>Watch this on the laptop while the Quest is in VR. Updated ~10&times;/s.</p>
<div id="status"></div>
<h2>Velocity setpoint preview (not sent to robot)</h2>
<div class="vel" id="vel"></div>
<h2>Head pose</h2>
<div class="head">
  <div class="compass"><div class="arrow" id="arrow"></div></div>
  <div class="vals" id="head"></div>
</div>
<h2>Raw data</h2>
<pre id="raw">waiting...</pre>
<script>
let lastTs = -1, lastChange = Date.now();
function fmt(n) { return (n >= 0 ? '+' : '') + n.toFixed(3); }
function fmt2(n) { return (n >= 0 ? '+' : '') + n.toFixed(2); }
async function poll() {
  try {
    const r = await fetch('/state', { cache: 'no-store' });
    const s = await r.json();
    if (s.ts !== lastTs) { lastTs = s.ts; lastChange = Date.now(); }
    const ageMs = Date.now() - lastChange;
    const stale = ageMs > 1000;
    document.getElementById('status').innerHTML = stale
      ? `<span class="stale">No fresh data for ${(ageMs/1000).toFixed(1)}s &mdash; is the Quest in VR and posting?</span>`
      : `Receiving live input.`;
    const v = s.velocity || { x: 0, y: 0, z: 0 };
    document.getElementById('vel').innerHTML =
      `<div><span>x (fwd+):</span> ${fmt(v.x)}</div>` +
      `<div><span>y (left+):</span> ${fmt(v.y)}</div>` +
      `<div><span>z (yaw left+):</span> ${fmt(v.z)}</div>`;

    const head = s.head, a = s.head_angles;
    if (head && a) {
      const p = head.position || { x: 0, y: 0, z: 0 };
      document.getElementById('head').innerHTML =
        `<div><span>pos x (m):</span> ${fmt2(p.x)}</div>` +
        `<div><span>pos y / height:</span> ${fmt2(p.y)}</div>` +
        `<div><span>pos z (m):</span> ${fmt2(p.z)}</div>` +
        `<div><span>yaw (left+):</span> ${a.yaw_deg.toFixed(0)}&deg;</div>` +
        `<div><span>pitch (up+):</span> ${a.pitch_deg.toFixed(0)}&deg;</div>`;
      // Compass arrow points where the head looks. Positive yaw = left, so the
      // on-screen arrow rotates counter-clockwise (negative CSS degrees).
      document.getElementById('arrow').style.transform =
        `translate(-50%,-100%) rotate(${-a.yaw_deg}deg)`;
    } else {
      document.getElementById('head').innerHTML = '<i>no head pose yet</i>';
    }
    document.getElementById('raw').textContent =
      JSON.stringify({ head: s.head, controllers: s.controllers }, null, 2);
  } catch (e) {
    document.getElementById('status').innerHTML =
      `<span class="stale">monitor fetch error: ${e}</span>`;
  }
}
setInterval(poll, 100);
poll();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP(S) server
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path == "/" or self.path.startswith("/index"):
            self._send(200, CAPTURE_PAGE.encode(), "text/html; charset=utf-8")
        elif self.path.startswith("/monitor"):
            self._send(200, MONITOR_PAGE.encode(), "text/html; charset=utf-8")
        elif self.path.startswith("/state"):
            self._send(200, json.dumps(read_state()).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if not self.path.startswith("/xr_input"):
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, b"bad json", "text/plain")
            return
        state = update_state(payload)
        v = state["velocity"]
        line = (
            f"[xr] controllers={len(state['controllers'])} "
            f"vel x={v['x']:+.3f} y={v['y']:+.3f} z={v['z']:+.3f}"
        )
        head = state.get("head")
        angles = state.get("head_angles")
        if head and angles:
            p = head.get("position") or {}
            line += (
                f" | head pos x={float(p.get('x', 0.0)):+.2f} "
                f"y={float(p.get('y', 0.0)):+.2f} z={float(p.get('z', 0.0)):+.2f} "
                f"yaw={angles['yaw_deg']:+.0f} pitch={angles['pitch_deg']:+.0f}"
            )
        if _session_log is not None:
            _session_log.record(line, state)
        else:
            print(line, flush=True)
        self._send(200, b"ok", "text/plain")

    def log_message(self, *args) -> None:  # silence default per-request logging
        pass


def ensure_cert(cert_dir: Path) -> tuple[Path, Path]:
    """Return (cert, key), generating a self-signed pair via openssl if missing."""
    cert = cert_dir / "cert.pem"
    key = cert_dir / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    cert_dir.mkdir(parents=True, exist_ok=True)
    print("Generating self-signed certificate (.certs/) ...")
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "365",
            "-nodes",
            "-subj",
            "/CN=go2-vr-debug",
        ],
        check=True,
    )
    return cert, key


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.12.1", 80))  # robot AP gateway; just selects a route
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def main(host: str = "0.0.0.0", port: int = 8443) -> None:
    global _session_log
    root = Path(__file__).resolve().parent.parent
    cert, key = ensure_cert(root / ".certs")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

    log_dir = root / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"vr_session_{datetime.now():%Y%m%d-%H%M%S}.log"
    _session_log = SessionLog(log_path)

    ip = get_local_ip()
    header = [
        "=" * 64,
        "Go2 VR joystick + head-pose debugger (HTTPS, no robot connection)",
        f"  started:       {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"  On the QUEST:  https://{ip}:{port}/        (accept the cert warning)",
        f"  On the LAPTOP: https://localhost:{port}/monitor",
        f"  logging to:    {log_path}",
        "=" * 64,
        "Ctrl-C to stop.",
    ]
    for entry in header:
        _session_log.line(entry)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _session_log.line("\nStopping.")
    finally:
        httpd.server_close()
        _session_log.close()


if __name__ == "__main__":
    main()
