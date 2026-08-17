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
- No suitable active public reverse-proxy/TLS route was observed for XC Body;
  deployment route, certificate, and firewall changes remain undecided and
  require explicit authorization.
- OpenClaw has local XC Body tool registration for the semantic and
  pending-thought bridges; no OpenClaw runtime/source patch was made.
- XC Body's app-only native-avatar firmware has been flashed at `0x20000`,
  preserving NVS. The factory image and tested rollback artifacts are retained
  privately.
- No camera or microphone path is in scope, and no capture endpoint is to be
  exposed during Milestone 1.
- Milestones 1 and 2 have historical physical acceptance. The current software
  focus is Milestone 3 continuity and reproducibility; the accepted runs did not
  capture a complete version set and are not a reproducible deployment claim.
- The selected upstream reference is `kisaragi-mochi/stackchan-mcp`, pinned at
  `804af573ba8f577f63efbd39f6e8a9c7f57b4647`; the local submodule is
  initialized at that exact revision.
- An isolated long-lived gateway and pending-thought service are deployed on
  the rendezvous VM. The source files are tracked, but the complete Python
  dependency set and service launch/supervisor definition are not. Deployment
  reproduction and automatic recovery therefore remain unverified;
  credentials and host configuration remain private.
- A dependency-free Python 3.10+ semantic core now provides strict v1 request
  validation, immutable symbolic recipes, injected device execution, and a
  mandatory idle attempt after expressive intents. Deployment packaging is
  still undecided.
- The v1 optional `speech` field is null-only. One transport-neutral semantic
  `embody` MCP descriptor/handler and an injected upstream MCP client wrapper
  are tested through fake calls.
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
- The injected loader verifies payload size, order, offsets, and hashes before
  one upstream `load_avatar_set` call. It contains no deployment URL, token, or
  archive path. The reviewed payload was loaded and physically verified; the
  helper itself remains environment-neutral.
- Native loading requires the reviewed gateway adaptation and an app-only
  firmware flash. Legacy 160x120 modes remain available for rollback, and the
  set must be reloaded after restart unless persistence is later proven.
- OpenClaw's managed MCP registry can consume remote Streamable HTTP servers
  with authentication headers, TLS verification, timeouts, and tool filters.
- Pinned `stackchan-mcp` `0.17.0` already supplies loopback `/mcp`, bearer and
  host validation, authenticated device WSS, a bounded command queue, and
  health/status endpoints. It was inspected remotely and locally at the exact
  pinned revision.
- The executable Milestone 2 service exposes only `consider_thought` over MCP
  stdio, keeps one upstream StackChan MCP session open, and owns one
  pending-thought runtime for that session lifetime.
- The service has no internal reconnect loop. Upstream transport loss exits the
  process; a safe reconnect follow-up must create a fresh runtime, restore
  avatar state before readiness, define pending-state loss, and provide a
  pinned dependency and launch/supervisor contract.
- Its accepted silent knock is `thinking`, pose `(12,50)` at low speed, a
  bounded hold, then `(0,43)` and `idle`. The current CoreS3 head pat or
  head stroke invokes prepared-audio playback. Duplicate playback is
  suppressed only for retained recent IDs in the running process; restart or
  eviction may replay it. No upstream `say` path remains.
- Local fake-port, full-suite, and real MCP SDK transport tests prove offer,
  knock, waiting, gesture acknowledgment, prepared-audio playback, idle,
  duplicate suppression, and clean draining of in-flight gesture work.
- Physical Milestone 2 acceptance passed on 2026-08-14: after the silent offer,
  Louis touched StackChan's head and confirmed the OpenClaw-prepared audio
  played through the device speaker. This is historical acceptance evidence;
  exact firmware, gateway, OpenClaw, and source versions were not all recorded,
  so reproduction requires a fresh versioned run.

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

1. Read `docs/MILESTONE_2.md` after this handoff.
2. Keep persistence, quiet hours, cooldowns, and event-selection policy out of
   Milestone 2.
3. Keep the persistent service private and expose only `consider_thought`.
4. Preserve separate upstream, downstream, and prepared-audio bearer tokens.
5. Treat the 2026-08-14 physical acceptance as the Milestone 2 baseline; rerun
   it after any transport, framing, firmware, or gesture-mapping change.

## Unknowns That Must Not Be Guessed

- Exact public domain, certificate/TLS termination choice, and credentials that
  will be provisioned for XC Body.
- Final service manager/container packaging and reviewed resource limits.
- Exact service dependency versions and launch/restart behavior; neither is
  reproducible from this repository yet.
- Current StackChan firmware and official-app compatibility requirement.
- Reviewed motion calibration for `pleased` and `concerned`.
- Whether the runtime-loaded avatar set survives any device or gateway restart.

## Definition of a Good Handoff

A new session should be able to understand the product goal, current milestone,
scope boundaries, architecture alternatives, upstream dependency, safety rules,
and next discovery steps without reading the `xc-buddy` conversation history.
