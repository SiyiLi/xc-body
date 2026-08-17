# Handoff

## Why This Repository Exists

The user has an existing OpenClaw agent and an official M5Stack StackChan
K151/CoreS3. The project goal is to make StackChan the agent's physical home
body, not a separate assistant and not a moving status screen.

The agreed North Star is:

> Build an embodied OpenClaw agent with continuity, initiative, and restraint.
> StackChan is its home body.

## Current State

- Branch: `main`
- GitHub origin: <https://github.com/SiyiLi/xc-body.git>
- OpenClaw and StackChan connect outbound to a separate always-on cloud
  rendezvous for Milestone 1.
- Read-only rendezvous discovery found Docker and sufficient headroom for an
  isolated XC Body service. Existing workloads and ports must remain untouched.
- Tencent blocks the unfiled `body.siyi.ai` domain before it reaches the VM.
  The selected personal-use route is instead `wss://43.143.37.91`, terminated
  by Caddy with an automatically renewed short-lived public IP certificate.
- OpenClaw registers the tracked local XC Body thought producer; no OpenClaw
  runtime/source patch was made.
- XC Body's app-only native-avatar firmware has been flashed at `0x20000`,
  preserving NVS. The factory image and tested rollback artifacts are retained
  privately.
- No camera or microphone path is in scope, and no capture endpoint is to be
  exposed during Milestone 1.
- Milestone 2 is complete; Milestone 3 is next.
- The selected upstream reference is `kisaragi-mochi/stackchan-mcp`, pinned at
  `804af573ba8f577f63efbd39f6e8a9c7f57b4647`; the local submodule is
  initialized at that exact revision.
- An isolated long-lived gateway and pending-thought service are deployed on
  the rendezvous VM. The tracked controller builds one `linux/amd64` runtime
  image from exact source, the pinned upstream revision, the reviewed avatar,
  and the gateway patch, then publishes it to TC Artifactory. The VM pulls the
  exact runtime and Caddy digests and runs only `xc-body-gateway`,
  `xc-body-pending`, and `xc-body-proxy`; credentials remain private.
- A dependency-free Python 3.10+ semantic core now provides strict v1 request
  validation, immutable symbolic recipes, injected device execution, and a
  mandatory idle attempt after expressive intents. The rendezvous VM packaging
  and launch path is now tracked under `deploy/` and
  `scripts/deploy.sh`.
- The v1 optional `speech` field is null-only. The executable semantic
  `embody` MCP service and its upstream MCP client wrapper are tested through
  fake calls.
- Native 320x240 faces were loaded and physically observed: idle, happy,
  thinking, sad, and surprised. A synchronized semantic `curious` run combined
  thinking with accepted pose `(12,50,30)` and returned automatically to idle.
- Reviewed measured calibration exists as an explicit deployment factory.
  Idle maps to upstream name `idle` at yaw `0`, pitch `43`, speed `30`; curious
  maps to `thinking` at command yaw `12`, pitch `50`, speed `30`, then exact
  idle. Louis judged that pose clearly visible and appropriately restrained.
- Upstream semantic faces map neutral to `idle`, attentive to `thinking`, happy
  to `happy`, and concerned to `sad`. Visible support is a separate calibration
  fact; the measured factory verifies all four for the accepted native payload.
- The upstream placeholder limitation was resolved by the reviewed native
  `layered-320x240` adaptation. The robot checksum-verified payload
  `daa35ed17a716860f0415c053dbf3d59e6e421ad50556de5ccc58cae28af36f7`.
- Complete semantic recipes fail before client calls when any face lacks visible
  verification. The measured factory verifies `idle`, `thinking`, `happy`, and
  `sad` only for the accepted native payload. Both semantic entry points restore
  the configured archive and require the exact recorded digest before executing
  a recipe; local calibration preflight still runs before endpoint loading.
- `Pleased` and `concerned` remain intentionally unaccepted; Louis stopped
  repetitive one-by-one physical calibration after the shared face, movement,
  and idle-return primitives were proven.
- A stdlib-only candidate avatar pipeline now builds the exact layered runtime
  payload: 14 complete native 320x240 RGB565-LE frames, a hash-bound
  manifest, and a labeled PNG contact sheet. Build outputs are ignored and
  reproducible.
- The generator verifies payload size, order, offsets, and hashes before
  writing assets. At service startup, the runtime loads the deployment archive
  and verifies the device-reported checksum against the reviewed payload.
- Native loading requires the reviewed gateway adaptation and an app-only
  firmware flash. Legacy 160x120 modes remain available for rollback, and the
  set must be reloaded after restart unless persistence is later proven.
- The tracked firmware patch now includes a USB-only maintenance channel for
  status, gateway URL/token updates, logs, and application reboot. The exact
  StackChan target builds successfully as firmware `2.2.6`. The app-only image
  was flashed at `0x20000` with NVS preserved; its SHA-256 is
  `c12ffb705d71c3ece5d78f3f2369c590b230a4c388432b5616c3ebfe671f175c`.
  USB status and application reboot are physically verified. The device has
  the saved authenticated endpoint `wss://43.143.37.91` and its token remains
  set.
- OpenClaw's managed MCP registry can consume remote Streamable HTTP servers
  with authentication headers, TLS verification, timeouts, and tool filters.
- Pinned `stackchan-mcp` `0.17.0` already supplies loopback `/mcp`, bearer and
  host validation, authenticated device WSS, a bounded command queue, and
  health/status endpoints. It was inspected remotely and locally at the exact
  pinned revision.
- The executable Milestone 2 boundary exposes only `consider_thought` over MCP
  stdio or authenticated Streamable HTTP. It keeps one upstream StackChan MCP
  session open and owns one pending-thought runtime for that session lifetime.
- Before serving, it restores and checksum-verifies the reviewed avatar, then
  binds readiness to the connected, initialized device session. A device
  session change makes readiness false and body actions fail closed.
- The service has no internal reconnect loop. Upstream transport loss exits the
  process; a safe reconnect follow-up must create a fresh runtime, preserve the
  existing startup restore, define pending-state loss, and verify supervisor
  restart behavior.
- Its accepted silent knock is `thinking`, pose `(12,50)` at low speed, a
  ten-second hold, then `(0,43)` and `idle`. The current CoreS3 head pat or
  head stroke invokes prepared-audio playback. Duplicate playback is
  suppressed only for retained recent IDs in the running process; restart or
  eviction may replay it. No upstream `say` path remains.
- Local contract, fake-port, and service tests cover offer,
  knock, waiting, gesture acknowledgment, prepared-audio playback, idle,
  duplicate suppression, and clean draining of in-flight gesture work.
## User Preferences and Constraints

- Keep changes small, cautious, and reviewable.
- Do not add features merely because they are technically possible.
- Avoid overengineering and imagined edge cases without user value.
- Explain root causes and architectural tradeoffs concretely.
- Do not modify or conflate the Stick S3/XC Buddy device with StackChan.
- Do not kill running applications unless explicitly requested.
- Do not flash hardware without explicit permission.
- Do not push or publish without explicit permission for that remote action.

## Product Reasoning Preserved from Earlier Exploration

The most inspiring StackChan projects treat the robot as a character with an
inner life rather than an API terminal. Relevant inspiration includes:

- Stackchan Alive: procedural face, breathing, gaze, touch, local character
  continuity, and privacy boundaries.
- BooBit: diary, household awareness, seasons, weather, drawings, and melodies.
- StackChan Matchday: a co-watching companion with opinions and reactions.
- stackchan-pet-fw: affection, touch reactions, audio streaming, and events.
- Dotty and Warble: local/self-hosted voice and agent stacks.

These are inspiration only. Do not import their feature scope into Milestone 1.

## Immediate Next Session

1. Read the Milestone 2 baseline before changing its transport or contract.
2. Start Milestone 3 only after restating its persistence and restraint scope.
3. Rerun the physical path after transport, firmware, voice, or gesture changes.

## Unknowns That Must Not Be Guessed

- Final supervisor restart policy and reviewed resource limits.
- Automatic recovery behavior after an upstream session or process failure.
- Reviewed motion calibration for `pleased` and `concerned`.
- Whether the runtime-loaded avatar set survives any device or gateway restart.

## Definition of a Good Handoff

A new session should be able to understand the product goal, current milestone,
scope boundaries, architecture alternatives, upstream dependency, safety rules,
and next discovery steps without reading the `xc-buddy` conversation history.
