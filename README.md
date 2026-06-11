# go2-control

Standalone control and video-streaming scripts for the Unitree Go2, built **on top of**
the [`go2_webrtc_driver`](https://github.com/legion1581/go2_webrtc_connect). The driver
is consumed as a dependency — this repo contains only the scripts.


##Demo on Meta Quest 3S VR headset
https://github.com/Us3r369/go_2_pro_teleoperation/issues/1#issue-4641359041
## Layout

```
scripts/
  sportmode_simple.py          # walk forward/back, sit, stand up/down (data channel)
  vui.py                       # LED brightness/color + volume control
  display_video_channel.py     # show the camera stream in an OpenCV window
  webrtc_headless_stream.py    # serve the camera as MJPEG over HTTP (no controls)
  remote_control.py            # web UI: live video + basic movement buttons
  remote_control_advanced.py   # web UI: live video + full action/trick controls
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
```

> The web-control scripts (`remote_control*.py`, `webrtc_headless_stream.py`) bind to
> `0.0.0.0:8080`, so anything on your local network can reach them. There is no
> authentication — only run them on a trusted network.
>
> Note: port 8080 is used instead of the more common 5000 because macOS reserves
> port 5000 for the AirPlay Receiver (Control Center).
