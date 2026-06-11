# Milestone 1 — "The Safe Driver" (reality-first)

The guiding structure for building the robot-facing core. **Tick the checkboxes as steps land**
and update each phase's **Status** line (see "Progress tracking" in `CLAUDE.md`).

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done.

## Goal

A single, tested `RobotCore` that owns all robot semantics (readiness, mode, clamp, deadman,
action preconditions, error veto) behind a `RobotPort` interface, so future front-ends
(PC/VR/BCI) become thin clients. Almost all of it must be testable **offline**.

## Guiding principle: reality-first, not assumption-first

We do **not** build the fake robot from code/docs guesses. We capture the **real robot's actual
behavior** first, and the fake is a fast, offline reproduction of that recorded reality. A
**contract test** then runs the same sequence against the real adapter and the fake and asserts
they agree — so the fake is *verified against reality*, and divergence (reality wins) flags a
wrong assumption.

Two layers of "real":
- **Protocol/telemetry** (response JSON, status codes, round-trip timing, `sportmodestate`
  actual velocity/pose, `lowstate` IMU/battery/foot-force) — machine-captured automatically.
- **Physical/embodied** (did it move, how far, stability, safety) — human-observed, with an
  optional external camera for posture/gait sessions.

Observation method: **automated probe = high-volume channel; the human = low-volume safety
oracle.** The human stays in the loop as safety supervisor for every hardware session.

---

## Phase A — Foundation (offline, no robot)

**Status:** `[ ]`

- [ ] **A1 — Dev scaffolding.** Create `core/` + `tests/` packages; install `pytest`,
  `pytest-asyncio`, `ruff` as dev deps; confirm empty `pytest` and `ruff check` run green.
- [ ] **A2 — Domain vocabulary (pure data).** `Velocity(vx,vy,wz)`, `Action` enum, `Mode`
  (`normal`/`ai`), telemetry dataclasses (`BatteryState`, `Fault`, `RobotState`), `CommandResult`.
- [ ] **A3 — `RobotPort` interface.** Capability-grouped Protocol/ABC + `raw` escape hatch.
  No implementation. (See `docs/robot-interface-reference.md` for the full surface.)

## Phase B — Touch reality first (robot required, human-supervised)

**Status:** `[ ]`

- [ ] **B1 — Instrumentation probe.** Script that sends one command and logs everything
  machine-observable to JSONL: full response, status code, round-trip time, and a telemetry
  window (`sportmodestate` + `lowstate`) captured before → during → after.
- [ ] **B2 — Thin `Go2RobotAdapter`.** Minimal `RobotPort` over the driver: connect + readiness,
  `set_velocity`, `do_action`, `get_mode`/`set_mode`, telemetry `subscribe`, `raw`. **No logic.**
- [ ] **B3 — Supervised characterization session.** Run the probe across a command battery
  (mode query/switch; `Move` at a few velocities; stop; sit/stand; hello; damp) with the human
  supervising and describing physical behavior. Capture the corpus + human notes.
- [ ] **B4 — Characterization findings.** Write up observed timings, response shapes,
  preconditions, and **surprises vs. our assumptions** (drives corrections everywhere).

## Phase C — Reality-grounded fake

**Status:** `[ ]`

- [ ] **C1 — `FakeRobotPort` from the corpus.** Replays observed responses, timings, and
  telemetry — built from Phase B data, not from assumptions.
- [ ] **C2 — Contract test.** Same command sequence run against the real adapter and the fake
  must agree. The real-hardware leg is gated behind a marker/flag so the suite still runs
  offline; the fake leg always runs.

## Phase D — Safe core (test-first against the reality-grounded fake)

**Status:** `[ ]`

- [ ] **D1 — Readiness gate.** Reject/queue commands until `is_ready`.
- [ ] **D2 — Mode arbitration.** Query/set; serialized; switch-once; never interleave motion
  with a switch.
- [ ] **D3 — Velocity clamp.** Clamp `command_velocity` to configured limits.
- [ ] **D4 — Deadman watchdog.** Stream the latest setpoint at ~10 Hz; zero velocity after
  N ms of silence. Inject a clock for deterministic tests.
- [ ] **D5 — Discrete actions.** Preconditions (must be standing) + mutual exclusion (no action
  mid-action; no flip outside `ai` / mid-walk).
- [ ] **D6 — Telemetry + veto.** Ingest battery/errors; expose latest `RobotState`; block risky
  commands on low battery / active fault.
- [ ] **D7 — Invariant suite.** Consolidated, comprehensive invariants (clamp, deadman, mode-once,
  no-interleave, veto, readiness) all green vs the fake.

## Phase E — Integration & close-out

**Status:** `[ ]`

- [ ] **E1 — Synthetic hardware runner.** `examples/run_core_synthetic.py`: adapter → core,
  scripted sequence (connect → ready → forward via streamed setpoints → stop → hello → damp),
  verified on the real robot.
- [ ] **E2 — Docs.** `docs/core-architecture.md`; update `CLAUDE.md`; note how existing scripts
  will re-point at the core (Milestone 2).
- [ ] **E3 — Acceptance check.** All criteria below pass.

---

## Acceptance criteria (Milestone 1 done when all true)

- [ ] `pytest` is green with **no robot connected**.
- [ ] `ruff format` + `ruff check` are clean.
- [ ] The contract test passes (fake leg always; real leg when hardware present).
- [ ] The synthetic runner drives the **real** robot through the core.
- [ ] The fake is demonstrably derived from captured real traces, not assumptions.

## Explicitly deferred to later milestones

Network transport (websocket/ROS2); PC/VR/BCI front-ends; hold-to-walk UI; multi-controller
arbitration policy; stubbed capability domains (lights/audio/perception/SLAM). All remain
reachable in the meantime via the `RobotPort` `raw` escape hatch.

## Risk note

We can only characterize what we can safely exercise. Flips/jumps and `LOW_CMD` are recorded
cautiously or stubbed — the corpus will be rich for locomotion/posture/mode and thin for the
risky edges, which is the intended risk tradeoff.
