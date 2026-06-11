# Go2 Robot Interface Reference

A holistic map of **everything the `go2_webrtc_driver` exposes** — every outbound command,
every inbound telemetry stream, the media channels, and the transport-level machinery — so
core/feature development can be designed against the full surface, not just what today's
scripts happen to use.

> Source: exploration of the `go2_webrtc_driver` package (`constants.py`, `webrtc_driver.py`,
> `webrtc_datachannel.py`, `webrtc_video.py`, `webrtc_audio.py`, `webrtc_audiohub.py`,
> `msgs/*.py`) and the driver's `examples/`. Items marked _(inferred)_ are named in the
> driver but not demonstrated, so their exact semantics are unconfirmed. Treat this as a
> living document — correct it as behavior is verified on hardware.

## The four underlying mechanisms

Every "port" below is one of just four shapes. A core abstraction only needs to wrap these
four; the dozens of topics are typed convenience methods on top.

1. **Request / response** — `pub_sub.publish_request_new(topic, {"api_id", "parameter"})`
   returns a response carrying a status code. Used for **commands** and **queries**.
2. **Pub / sub** — `pub_sub.subscribe(topic, callback)` / `unsubscribe(topic)`.
   Used for **telemetry** streams.
3. **Media tracks** — separate WebRTC tracks for video / audio, toggled with
   `switchVideoChannel/AudioChannel(True/False)` and consumed via `add_track_callback`.
4. **Transport meta** — validation, heartbeat, network status, error stream, file transfer;
   managed inside `WebRTCDataChannel`.

**`api_id`s are namespaced per topic.** `api_id: 1002` means "set mode" on `MOTION_SWITCHER`
but "set volume"-class operations on `VUI`. Always pair an `api_id` with its topic.

The universal command envelope:

```python
await conn.datachannel.pub_sub.publish_request_new(
    RTC_TOPIC["SPORT_MOD"],
    {"api_id": SPORT_CMD["Move"], "parameter": {"x": 0.5, "y": 0, "z": 0}},
)
```

The universal telemetry intake:

```python
conn.datachannel.pub_sub.subscribe(RTC_TOPIC["LOW_STATE"], my_callback)
```

---

## Outbound — commands & queries (you → robot)

All via `publish_request_new(RTC_TOPIC[...], {...})`.

| Domain | Topic (`RTC_TOPIC`) | What it does | Notes / safety |
|---|---|---|---|
| **Locomotion** | `SPORT_MOD` + `SPORT_CMD["Move"]` | velocity setpoint `{x,y,z}` (continuous; persists until changed) | the clamp + deadman target |
| **Posture** | `SPORT_MOD` | Damp, BalanceStand, StandUp, StandDown, Sit, RiseSit, RecoveryStand | preconditions matter (must balance before moving) |
| **Tricks** | `SPORT_MOD` | Hello, Stretch, Dance1, Dance2, WiggleHips, FingerHeart, Scrape, Wallow | self-terminating animations |
| **Athletic** | `SPORT_MOD` | FrontFlip, BackFlip, LeftFlip, RightFlip, FrontJump, FrontPounce, Handstand, StandOut, Bound, MoonWalk, CrossWalk, OnesidedStep, CrossStep | ⚠️ need `ai` mode + clear space + battery |
| **Gait / trim** | `SPORT_MOD` | Euler, BodyHeight, FootRaiseHeight, SpeedLevel, SwitchGait, ContinuousGait, EconomicGait, Pose, SwitchJoystick, Trigger | continuous trims / config |
| **Getters** | `SPORT_MOD` | GetState, GetBodyHeight, GetFootRaiseHeight, GetSpeedLevel | query-style |
| **Motion mode** | `MOTION_SWITCHER` | `api_id 1001` query current mode; `api_id 1002` set mode (`normal` / `ai`) | slow (~3–10s), exclusive switch; gates which `SPORT_CMD`s are valid |
| **Lights / UI** | `VUI` | brightness (1005 set / 1006 get), LED color (1007), volume (1003 set / 1004 get) | harmless; good connectivity test |
| **Audio** | `AUDIO_HUB_REQ` | list / play / pause / resume / play-mode / rename / delete, file **upload**, **megaphone** enter/exit/upload | wrapped by `WebRTCAudioHub`; ⚠️ megaphone drives the speaker |
| **Obstacle avoid** | `OBSTACLES_AVOID` | enable / disable onboard obstacle avoidance | relevant to shared-autonomy / BCI |
| **LiDAR** | `ULIDAR_SWITCH` (+ `set_decoder`) | turn voxel-map stream on / off | requires `disableTrafficSaving(True)` or bandwidth starves |
| **UWB** | `UWB_REQ` | ultra-wideband (follow / ranging) on / off | |
| **Gas sensor** | `GAS_SENSOR_REQ` | sensor control (model-dependent) | |
| **Camera still** | `FRONT_PHOTO_REQ` | capture a front photo (videohub) | |
| **Low-level** | `LOW_CMD` | raw per-joint motor command | ⚠️⚠️ bypasses the safety controller — can damage/destabilize the robot |
| **Joystick** | `WIRELESS_CONTROLLER` | wireless controller passthrough | could serve as an input source |
| **Shell** | `BASH_REQ` (bashrunner) | run a command on the robot | ⚠️⚠️ arbitrary code execution — lock down / disable by policy |
| **Arm** | `ARM_COMMAND` | arm control (if equipped) | hardware-dependent |
| **SLAM / Nav** | `SLAM_QT_COMMAND`, `SLAM_ADD_NODE`, `SLAM_ADD_EDGE`, `LIDAR_MAPPING_CMD` | mapping / pose-graph commands | future autonomy |
| **Misc** _(inferred)_ | `PROGRAMMING_ACTUATOR_CMD`, `ASSISTANT_RECORDER` | present in topic table; semantics not demonstrated | low confidence |

### Full `SPORT_CMD` api_id table

```
Damp 1001            BalanceStand 1002    StopMove 1003        StandUp 1004
StandDown 1005       RecoveryStand 1006   Euler 1007           Move 1008
Sit 1009             RiseSit 1010         SwitchGait 1011      Trigger 1012
BodyHeight 1013      FootRaiseHeight 1014 SpeedLevel 1015      Hello 1016
Stretch 1017         TrajectoryFollow 1018 ContinuousGait 1019 Content 1020
Wallow 1021          Dance1 1022          Dance2 1023          GetBodyHeight 1024
GetFootRaiseHeight 1025 GetSpeedLevel 1026 SwitchJoystick 1027 Pose 1028
Scrape 1029          FrontFlip 1030       FrontJump 1031       FrontPounce 1032
WiggleHips 1033      GetState 1034        EconomicGait 1035    FingerHeart 1036
StandOut 1039        Handstand 1301       CrossStep 1302       OnesidedStep 1303
Bound 1304           MoonWalk 1305        LeftFlip 1042        RightFlip 1043
BackFlip 1044        LeadFollow / FreeWalk 1045   Standup 1050  CrossWalk 1051
```

(Note: a couple of ids collide in the driver constants — e.g. `LeadFollow` and `FreeWalk`
are both `1045` — verify on hardware before relying on them.)

---

## Inbound — telemetry (robot → you, via `subscribe`)

| Domain | Topic (`RTC_TOPIC`) | Payload (from examples) | Relevance |
|---|---|---|---|
| **Health / proprioception** | `LOW_STATE` (`rt/lf/lowstate`) | IMU roll/pitch/yaw, per-motor `q`/temperature/lost, **BMS battery** (SOC %, current, cycle count, NTC temps), foot force, power voltage | **high** — battery + thermal gate commands |
| **Aggregated state** | `MULTIPLE_STATE` | body height, brightness, foot-raise height, obstacle-avoid on/off, speed level, UWB on/off, volume | **high** — current config/mode snapshot |
| **Motion state** | `SPORT_MOD_STATE` (`rt/sportmodestate`), `LF_SPORT_MOD_STATE` | pose, velocity, gait, body height, foot positions | **high** — closed-loop feedback |
| **Perception (LiDAR)** | `ULIDAR` (`voxel_map`), `ULIDAR_ARRAY` (`voxel_map_compressed`), `ULIDAR_STATE` | voxel map (raw / compressed), lidar status | medium |
| **Odometry** | `ROBOTODOM` (`rt/utlidar/robot_pose`) | robot pose | medium |
| **Localization / mapping** | `SLAM_ODOMETRY`, `LIDAR_LOCALIZATION_ODOM`, `LIDAR_MAPPING_ODOM`, `LIDAR_MAPPING_CLOUD_POINT`, `LIDAR_MAPPING_PCD_FILE`, `LIDAR_NAVIGATION_GLOBAL_PATH`, `LIDAR_LOCALIZATION_CLOUD_POINT`, `GRID_MAP`, `SLAM_PC_TO_IMAGE_LOCAL`, `SLAM_QT_NOTICE` | SLAM / nav outputs | future autonomy |
| **System** | `SERVICE_STATE`, `SELF_TEST` | running services, self-test results | medium |
| **Errors** | data-channel types `ADD_ERROR` / `RM_ERROR` / `ERRORS` (decoded by `msgs/error_handler.py` against `app_error_messages`) | fault codes → human text | **high** — safety veto |
| **Sensors** | `GAS_SENSOR`, `UWB_STATE` | gas reading, UWB state | low |
| **Audio** | `AUDIO_HUB_PLAY_STATE` | playback state | low |
| **AI** | `GPT_FEEDBACK` | onboard GPT-flow feedback | low |
| **Arm** | `ARM_FEEDBACK` | arm state | hardware-dependent |

### Error / fault sources (`app_error_messages`)

Decoded fault domains the robot can report — surface these as a safety veto:

- **Comms** (source 100/200): DDS timeout, MCU/motor/battery comms, remote-control comms.
- **Motor** (source 300): overcurrent, overvoltage, driver/winding overheating, encoder abnormal,
  bus undervoltage, comms interruption.
- **Radar** (source 400): pointcloud abnormal, serial-port data abnormal, dirt index.
- **UWB** (source 500): serial open abnormal, info-retrieval abnormal.
- **Motion control** (source 600): overheating software protection, **low-battery software protection**.
- **Wheel** variants (300_40/80/100): calibration data, abnormal reset, motor comms interruption.

---

## Media channels (separate from the data channel)

These ride dedicated WebRTC tracks, not the pub/sub data channel.

- **Video in** — `conn.video.switchVideoChannel(True)` + `conn.video.add_track_callback(cb)`;
  frames arrive as decodable video frames (→ `frame.to_ndarray(format="bgr24")`).
- **Audio in** — `conn.audio.switchAudioChannel(True)` + `add_track_callback`;
  demonstrated by the live-receive / save-to-file / internet-radio examples.
- **Audio out** — speaker / megaphone via `WebRTCAudioHub` (`upload_audio_file`, play by uuid,
  `enter_megaphone` / `exit_megaphone`, `upload_megaphone`).
- **Still photo** — `FRONT_PHOTO_REQ` (single front-camera capture).

---

## Transport / meta the core must OWN (don't expose to front-ends)

Managed inside `WebRTCDataChannel` / `msgs/*`:

- **Validation gate** (`on_validate` → `data_channel_opened = True`) — the readiness signal;
  no command works before it fires.
- **Heartbeat** (`msgs/heartbeat.py`) — liveness; loss ⇒ link dead ⇒ trigger the deadman.
- **Network status** (`msgs/rtc_inner_req.py` → `on_network_status`) — connection mode (STA/AP/relay).
- **`disableTrafficSaving(True)`** — required before subscribing to LiDAR.
- **Error stream** (`ADD_ERROR`/`RM_ERROR`/`ERRORS` + `error_handler.py`) — fault decoding.
- **File upload / download** (`rtc_inner_req` file uploader/downloader) — e.g. audio uploads.
- **Decoder selection** (`set_decoder('libvoxel' | 'native')`) — LiDAR voxel decoding.
- **Pub/sub primitives** — `publish`, `publish_request_new` (with future-based response
  resolution), `subscribe` / `unsubscribe`, `publish_without_callback`.

---

## Connection methods (`WebRTCConnectionMethod`)

- **`LocalAP`** — this machine joins the robot's own Wi-Fi hotspot; fixed IP `192.168.12.1`.
- **`LocalSTA`** — robot on the same LAN; pass `ip=...`, or a `serialNumber` (resolved via
  multicast discovery, `multicast_scanner.discover_ip_sn`).
- **`Remote`** — cloud relay via TURN/STUN with an auth token (`fetch_token`, `fetch_public_key`,
  `fetch_turn_server_info`).

Robot-side signaling ports: **8081** (`/offer`, newer firmware) and **9991** (`/con_notify`,
older). Multicast discovery: ports **10131** (query) / **10134** (response).

---

## Safety-sensitive surface (gate or disable by policy)

- **`LOW_CMD`** — raw motor control; bypasses the balance/safety controller.
- **`BASH_REQ`** — arbitrary command execution on the robot.
- **Athletic moves** (flips, jumps, handstand) — require `ai` mode, clear space, adequate battery.
- **Megaphone / audio out** — drives the physical speaker.
- **Mode switches** — slow physical transitions; serialize and never interleave with motion.

---

## Implication for the core abstraction

Design `RobotPort` as **capability-grouped sub-interfaces over the four mechanisms**, so the
contract spans the whole surface even when only a slice is implemented:

```
RobotPort
├─ lifecycle   : connect, is_ready, on_heartbeat_lost      ← owns validation + heartbeat
├─ locomotion  : set_velocity(vx, vy, wz)                  ← continuous
├─ posture     : stand / sit / damp / recover …            ← discrete, precondition-checked
├─ skills      : hello / dance / flip … (mode-gated)       ← discrete
├─ mode        : get_mode / set_mode                       ← serialized, exclusive
├─ lights/audio: vui, audiohub                             ← typed stub initially
├─ perception  : lidar / photo / sensors on/off + streams  ← typed stub initially
├─ telemetry   : subscribe(state) → battery, pose, errors  ← wire battery + errors first
└─ raw         : publish_request_new / subscribe           ← escape hatch: ANY topic, day one
```

Two payoffs:

1. The **`raw` escape hatch** keeps every topic in this document reachable immediately, even
   before it has a typed method — so new capabilities never require a core redesign.
2. The **safety-sensitive ports** are identified up front, so the core can gate or disable
   them by policy rather than discovering them ad hoc later.
