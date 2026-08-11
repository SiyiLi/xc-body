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
- The rendezvous deployment state has not yet been inventoried for XC Body.
- Unrelated workloads on the shared host must remain isolated from XC Body.
- No OpenClaw configuration has been changed for XC Body.
- No StackChan firmware has been flashed for XC Body.
- No camera or microphone path is in scope, and no capture endpoint is to be
  exposed during Milestone 1.
- The current focus is Milestone 1: manual deterministic embodiment.
- The selected upstream reference is `kisaragi-mochi/stackchan-mcp`, pinned at
  `804af573ba8f577f63efbd39f6e8a9c7f57b4647`; the local submodule is currently
  uninitialized.
- Runtime language and packaging are intentionally undecided until the VM
  inventory and pinned upstream reuse boundary are inspected.

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
2. With explicit permission, perform a read-only inventory of the OpenClaw host
   and cloud rendezvous host before proposing changes.
3. Inventory existing workload boundaries, service or container layout,
   reverse-proxy routes, TLS termination, firewall, health checks, and resource
   headroom without disturbing unrelated workloads.
4. Inspect the pinned `stackchan-mcp` revision only enough to verify the
   intended Streamable HTTP gateway and firmware path.
5. Discover the installed StackChan firmware version and recovery path before
   proposing a flash.
6. Confirm the authenticated outbound MCP HTTP and WSS paths using inventory
   evidence rather than assumed domains, ports, or infrastructure.
7. Propose the smallest cloud-only implementation slice and fake-device test
   story before coding.

## Unknowns That Must Not Be Guessed

- OpenClaw version, launch method, and MCP configuration.
- Cloud host package and container state, service manager, reverse-proxy type
  and routes, TLS provider, firewall state, and currently open ports.
- Existing workload resource use and capacity available to an isolated XC Body
  service.
- Domain ownership and the credentials that will be provisioned for XC Body.
- Current StackChan firmware and official-app compatibility requirement.
- Exact neutral servo pose and visually attractive expression recipes.

## Definition of a Good Handoff

A new session should be able to understand the product goal, current milestone,
scope boundaries, architecture alternatives, upstream dependency, safety rules,
and next discovery steps without reading the `xc-buddy` conversation history.
