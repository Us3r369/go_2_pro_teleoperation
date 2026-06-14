# Unitree Go2 — Control & Streaming Scripts

Standalone control and video-streaming scripts for the Unitree Go2, built on top of
the [`go2_webrtc_connect`](https://github.com/legion1581/go2_webrtc_connect) driver
(pulled in as a dependency — this repo is the scripts layer).

The project explores different ways to pilot and perceive through the robot. Today it
supports driving the Go2 and viewing its video feed from a **laptop** or a **VR headset**,
both through a single webpage.

## Roadmap
- **Next — VR telepresence:** a bot-mounted, servo-actuated camera streamed to the
  headset, with the view following either head rotation (head-coupled) or joystick input
  for look-around control.
- **Long term — BCI control:** an experimental brain–computer interface as an additional
  input channel.

Ideas and suggestions for other control or feedback channels are welcome.


## Demo on Meta Quest 3S VR headset
[https://github.com/Us3r369/go_2_pro_teleoperation/issues/1#issue-4641359041](https://github.com/user-attachments/assets/1c7acdb2-2bc8-4a18-95b1-227625456314)
## Layout

```
scripts/
  sportmode_simple.py          # walk forward/back, sit, stand up/down (data channel)
  vui.py                       # LED brightness/color + volume control
  display_video_channel.py     # show the camera stream in an OpenCV window
  webrtc_headless_stream.py    # serve the camera as MJPEG over HTTP (no controls)
  remote_control.py            # web UI: live video + basic movement buttons
  remote_control_advanced.py   # web UI: live video + full action/trick controls
  vr_joystick_debug.py         # HTTPS WebXR page: read Quest 3/3S thumbsticks + head pose (no robot motion)
```

## Setup

These scripts depend on the Go2 WebRTC driver, installed in **editable** mode from a
sibling checkout, so the two repos stay cleanly separated.

```bash
# 1. (recommended) create a virtualenv
python -m venv .venv && source .venv/bin/activate

# 2. install the driver (editable, from the sibling clone)
pip install -e ../go2_webrtc_connect

# 3. install this project's deps
pip install -r requirements.txt        # or: pip install -e .
```

## Running

Each script hard-codes the robot IP near the top — edit the `Go2WebRTCConnection(...)`
line to match your robot before running.

```bash
python scripts/sportmode_simple.py
python scripts/display_video_channel.py
python scripts/remote_control_advanced.py     # then open http://localhost:8080
python scripts/vr_joystick_debug.py           # then open the printed https:// URL in the headset
```

> `vr_joystick_debug.py` is stdlib-only and never commands the robot — it serves a WebXR
> page over HTTPS (WebXR requires a secure context) and logs the headset's thumbstick and
> head-pose input to `logs/`. It's a debug/telemetry harness for the upcoming VR control
> work, runnable without the robot connected.

> The web-control scripts (`remote_control*.py`, `webrtc_headless_stream.py`) bind to
> `0.0.0.0:8080`, so anything on your local network can reach them. There is no
> authentication — only run them on a trusted network.
>
> Note: port 8080 is used instead of the more common 5000 because macOS reserves
> port 5000 for the AirPlay Receiver (Control Center).
