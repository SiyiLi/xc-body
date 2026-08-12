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
- No OpenClaw configuration has been changed for XC Body.
- No StackChan firmware has been flashed for XC Body.
- No camera or microphone path is in scope, and no capture endpoint is to be
  exposed during Milestone 1.
- The current focus is Milestone 1: manual deterministic embodiment.
- The selected upstream reference is `kisaragi-mochi/stackchan-mcp`, pinned at
  `804af573ba8f577f63efbd39f6e8a9c7f57b4647`; the local submodule is currently
  uninitialized.
- Deployment packaging remains undecided. The inventory supports an isolated
  container/service, but no deployment was created.
- A dependency-free Python 3.10+ semantic core now provides strict v1 request
  validation, immutable symbolic recipes, injected device execution, and a
  mandatory idle attempt after expressive intents. Deployment packaging is
  still undecided.
- The v1 optional `speech` field is null-only. One transport-neutral semantic
  `embody` MCP descriptor/handler and an injected upstream MCP client wrapper
  are tested through fake calls.
- A real cloud-to-device `curious` command moved the head and returned it to
  exact neutral. It did not visibly change the display, so the full semantic
  behavior did not pass.
- Reviewed measured calibration exists as an explicit deployment factory.
  Idle maps to upstream name `idle` at yaw `0`, pitch `43`, speed `30`; curious
  maps to `thinking` at command yaw `3`, pitch `45`, speed `30`, then exact
  idle. The observed curious pose was yaw `1`, pitch `44` before settling to
  `0`, `43`.
- Upstream semantic faces map neutral to `idle`, attentive to `thinking`, happy
  to `happy`, and concerned to `sad`. Visible support is a separate calibration
  fact, and the measured factory marks zero faces visibly verified.
- The pinned revision and `firmware-v1.16.0` tag contain 1x1 black placeholder
  avatar assets. Firmware `ok=true` confirmed LVGL application, not visible
  rendering. Human observation found no face change for tested avatar names.
- Complete semantic recipes now fail before client calls when any face lacks
  visible verification. The runner performs this preflight before loading
  endpoint configuration or opening an MCP session.
- `Pleased` and `concerned` also lack reviewed motion calibration. Full
  acceptance criteria 1-4 remain blocked until real assets are installed and
  visibly verified.
- OpenClaw's managed MCP registry can consume remote Streamable HTTP servers
  with authentication headers, TLS verification, timeouts, and tool filters.
- Pinned `stackchan-mcp` `0.17.0` already supplies loopback `/mcp`, bearer and
  host validation, authenticated device WSS, a bounded command queue, and
  health/status endpoints. It was inspected remotely at the exact revision;
  the local submodule remains uninitialized.

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

The next Codex session should:

1. Read the repository in the order specified by `AGENTS.md`.
2. Review the completed read-only OpenClaw, rendezvous, and upstream
   discovery recorded here and in `docs/ARCHITECTURE.md`. Do not repeat or
   mutate it.
3. Discover the installed StackChan firmware version, exact hardware revision,
   official recovery image/path, and official-app compatibility requirement
   before proposing a flash.
4. Prepare real face assets for review, then install them only with explicit
   flash permission and verify every enabled face by human observation.
5. Design—but do not deploy—the isolated service/container and TLS routes using
   the discovered host constraints. Raw ports remain private.
6. Prepare the minimal OpenClaw MCP server definition and reviewed tool
   allowlist. Do not apply config until explicitly authorized.
7. After visible faces pass, calibrate the remaining motions on real hardware
   only with the required hardware permission.

## Unknowns That Must Not Be Guessed

- Exact public domain, certificate/TLS termination choice, and credentials that
  will be provisioned for XC Body.
- Final service manager/container packaging and reviewed resource limits.
- Current StackChan firmware and official-app compatibility requirement.
- Reviewed motion calibration for `pleased` and `concerned`.

## Definition of a Good Handoff

A new session should be able to understand the product goal, current milestone,
scope boundaries, architecture alternatives, upstream dependency, safety rules,
and next discovery steps without reading the `xc-buddy` conversation history.
