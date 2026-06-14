# CLAUDE.md — go2-control

Guidance for Claude (and humans) working in this repo. Read this first.

## What this project is

Standalone Python scripts to **control and stream video from a Unitree Go2 quadruped**
over its WebRTC interface (the same path the mobile app uses). The scripts are thin
clients built **on top of** the [`go2_webrtc_driver`](https://github.com/legion1581/go2_webrtc_connect),
which does the heavy lifting (auth handshake, SDP/ICE, data-channel pub/sub, media tracks).

This repo contains **only the scripts** — the driver is consumed as a dependency, not
vendored or modified (see "The driver dependency" below).

**Future direction (not built yet):** the long-term goal is a single generic entry point
into the robot — a stable command/telemetry contract (ports-and-adapters style) supporting
both *continuous* inputs (velocity setpoints: joystick, VR head-pose, BCI intent) and
*discrete* inputs (actions: sit, stand, tricks) — so multiple front-ends (PC, VR headset,
BCI) can drive the robot through the same core. Keep this in mind when structuring new
code, but don't over-engineer toward it prematurely; today's reality is the scripts below.

## Repository layout

```
scripts/
  sportmode_simple.py          # scripted walk/sit/stand sequence (data channel) — MOVES THE ROBOT
  vui.py                       # LED brightness/color + volume (no motion — safest test)
  display_video_channel.py     # show camera stream in an OpenCV window (needs a GUI/display)
  webrtc_headless_stream.py    # serve camera as MJPEG over HTTP (no controls)
  remote_control.py            # web UI: live video + basic movement buttons — MOVES THE ROBOT
  remote_control_advanced.py   # web UI: video + full action/trick set (incl. flips) — MOVES THE ROBOT
  vr_joystick_debug.py         # HTTPS WebXR page: read Quest 3/3S thumbsticks + 6DoF head pose, stream to laptop, log to logs/ — NO ROBOT (stdlib only)
tests/
  test_vr_joystick_debug.py    # hardware-free unit tests for the VR input→velocity mapping
pyproject.toml                 # project metadata + deps (scripts-only, no importable package)
requirements.txt               # runtime deps (flask, opencv-python, numpy)
README.md
CLAUDE.md                      # this file
docs/
  robot-interface-reference.md # full map of every driver port: commands, telemetry, media, meta
  milestone-1-plan.md          # active implementation plan (the "Safe Driver" core) — tick as you go
```

## Reference docs

- **`docs/robot-interface-reference.md`** — holistic catalog of the entire `go2_webrtc_driver`
  surface: all outbound command topics/`api_id`s, all inbound telemetry streams, media channels,
  transport/meta machinery, connection methods, and the safety-sensitive ports. Consult this when
  designing the core or wiring any new robot capability, so work is built against the full
  interface rather than just what current scripts use.

## The driver dependency

- The driver lives in a **sibling checkout**: `../go2_webrtc_connect`, installed **editable**:
  `pip install -e ../go2_webrtc_connect`.
- **Do not modify the driver** to make a script work. It is treated as unmodified upstream.
  If a change truly belongs there, raise it explicitly rather than editing it silently —
  keeping the two repos cleanly separated is a deliberate design decision.
- It's fine (and encouraged) to *read* the driver source to understand behavior
  (e.g. `go2_webrtc_driver/webrtc_driver.py`, `constants.py`, `unitree_auth.py`,
  `multicast_scanner.py`).

## Environment & running

- Python **3.11** in a local venv at `.venv` (interpreter: `.venv/bin/python`).
- After any directory rename, the venv's baked-in absolute paths break — re-create or
  re-point it (we hit this once; see Gotchas).
- Run a script with the venv active, from the repo root:
  ```bash
  source .venv/bin/activate
  python scripts/vui.py
  ```
  or without activating: `.venv/bin/python scripts/vui.py`.

### Connecting to the robot

Each script selects a connection method near the top via `Go2WebRTCConnection(...)`:

- **`LocalAP`** (current default): this machine is joined to the robot's **own Wi-Fi
  hotspot**; the driver talks to the fixed IP `192.168.12.1`. No IP needed.
- **`LocalSTA`**: the robot is on the **same LAN** as this machine; pass `ip="..."`
  (or a serial number). Preferred for the multi-device future, since several machines
  can reach the robot at once.

The robot's signaling ports are **8081** (`/offer`) and **9991** (`/con_notify`); these
are on the robot, not configurable here.

## Hard-won gotchas (don't rediscover these)

- **Port 5000 is taken by macOS AirPlay Receiver** (Control Center). The web scripts
  therefore serve on **8080**, not 5000. Open `http://localhost:8080`.
- **macOS drifts off the robot's hotspot.** The Go2 AP has no internet, so macOS quietly
  switches back to a network that does. Symptom: `[Errno 65] No route to host` to
  `192.168.12.1`. Fix: rejoin the `Go2_…` SSID and verify with
  `ipconfig getifaddr en0` (should be `192.168.12.x`) and `ping 192.168.12.1`. To prevent
  drift, toggle off normal Wi-Fi while testing or raise the Go2 SSID priority.
- **Wi-Fi password with `!`**: wrap in single quotes in the shell
  (`networksetup -setairportnetwork en0 'Go2_XXXX' 'my!password'`) to avoid history expansion.
- **Renaming the project dir breaks the venv**: `activate`, `pip` shebangs and `pyvenv.cfg`
  hardcode the old absolute path. Either recreate the venv or rewrite the paths, then
  `hash -r` in any open shell.
- **`pyaudio` is unused**: it's in the driver's setup.py but never imported; the driver
  uses `sounddevice` (bundles portaudio). Don't fight a `pyaudio`/`portaudio.h` build —
  install the driver with `--no-deps` then the real deps minus pyaudio. `lz4` is an
  undeclared driver dep (lidar decoder) and must be installed too.
- **`objc ... libavdevice` warning at startup is benign** — a harmless duplicate between
  `av` and `cv2` ffmpeg libs. Not an error.

## Coding standards

- **Ruff** for both formatting and linting. Run before considering work done:
  ```bash
  ruff format . && ruff check .
  ```
  Fix lint findings rather than suppressing them, unless there's a documented reason.
- Follow **PEP 8**; add **type hints** to new functions.
- Keep functions small and single-purpose. Prefer pure, testable logic separated from
  I/O / network / robot calls (this also makes the fake-robot tests below possible).
- Match the style of surrounding code. No dead code, no commented-out blocks left behind.

## Testing — required for every new function/component

**Always test new or changed code before calling it done.** This is non-negotiable.

- **Framework: `pytest`.** Put tests under `tests/`, named `test_*.py`.
- **Hardware-free unit tests with a fake robot.** Robot interaction goes through the
  driver's connection object; for logic that decides *what* to send, inject a **fake/mock
  robot adapter** that records calls instead of talking to hardware, and assert on what
  would have been sent. This lets the full test suite run with **no robot and no network**.
- **Test the safety-critical invariants** when you add control logic, e.g.:
  - velocity is clamped to configured limits (no command exceeds max),
  - a watchdog/deadman zeroes velocity when input goes stale,
  - discrete actions aren't interleaved with an in-flight blocking action,
  - mode preconditions (e.g. switch to `normal` mode) happen exactly once, not per command.
- **Connection-dependent scripts** that genuinely need the robot get a documented
  **manual on-hardware checklist** (does it connect? does video arrive? does the command
  actuate?) — but extract and unit-test the non-I/O logic so most of it is covered offline.
- Run `pytest` (and `ruff`) before finishing a task.

## Safety — the robot physically moves

- `sportmode_simple.py`, the movement buttons in `remote_control*.py`, and especially the
  **flips/jumps** in `remote_control_advanced.py` cause real motion.
- **Never trigger a movement/trick command without explicit user confirmation** that the
  robot is on the floor with clear space and adequate battery. When testing, default to
  non-moving commands first (e.g. `vui.py`, mode/status reads).
- Only one process can hold the WebRTC connection at a time — stop one script before
  starting another. A stuck/half-open session is cleared by power-cycling the robot.

## Security — never commit sensitive information

- **Never commit secrets**: API keys, robot/account credentials, Wi-Fi passwords, serial
  numbers tied to an account, tokens, `.env` files. `.env` is git-ignored — keep it that way.
- **Don't hardcode credentials** in scripts. Robot IP / SSID are environment-specific;
  prefer leaving placeholders or reading from env/config over committing real values.
  (A real LAN IP isn't a secret, but account email/password for `Remote` mode is — never
  commit those.)
- Before any commit, scan the diff for accidentally included secrets.
- The web-control scripts bind to `0.0.0.0` with **no authentication** — only for trusted
  networks. Note this in anything that exposes them more widely; don't add real auth/secrets
  inline without a proper config story.

## Git practices

- Branch off the default branch for changes; don't commit directly to it.
- **Only commit or push when the user explicitly asks.**
- Keep the upstream driver repo (`../go2_webrtc_connect`) clean — changes go here, not there.
- End commit messages with the required `Co-Authored-By` trailer.
