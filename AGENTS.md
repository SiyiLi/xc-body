# Instructions for Coding Agents

## Read Order

Before proposing or making changes, read:

1. `README.md`
2. `docs/HANDOFF.md`
3. `docs/MILESTONE_1.md`
4. `docs/ARCHITECTURE.md`
5. `docs/DECISIONS.md`
6. `docs/ROADMAP.md`

Inspect `references/stackchan-mcp` only for the specific capability being
implemented. It is a pinned reference dependency, not a place for casual edits.

## Current Phase

The current phase is Milestone 1: manual, deterministic embodiment from an
existing OpenClaw agent to the official M5Stack StackChan K151/CoreS3.

Keep the scope limited to:

- Secure connectivity.
- Device health and reconnect behavior.
- `idle`, `curious`, `pleased`, and `concerned` intentions.
- Deterministic expression recipes.
- Safe return to idle.

Do not implement cron jobs, autonomy, memory, pending thoughts, touch-to-agent
events, microphone, camera, vision, Home Assistant, Stick S3 integration, or a
mobile app during Milestone 1.

## Known Deployment Fact

OpenClaw and StackChan connect to a separate always-on cloud rendezvous host.
Its XC Body deployment surface must be inventoried before implementation. Do
not infer host configuration that has not been inspected.

## Engineering Rules

- Think before coding and state assumptions.
- Prefer the smallest implementation that passes the milestone acceptance
  tests.
- Reuse the pinned `stackchan-mcp` hardware layer before writing firmware or
  device protocols.
- The agent chooses semantic intentions; deterministic code owns expressions,
  timing, LEDs, and servo motion.
- Treat servo safety and reconnect recovery as correctness requirements.
- Keep secrets and personal assets out of Git.
- Preserve unrelated user changes.
- Do not modify `xc-buddy` from this repository.
- Do not flash hardware without explicit permission for that flash.
- Do not install or reconfigure OpenClaw or the cloud rendezvous host without
  explicit permission.
- Do not create a remote repository, push, publish, or upload artifacts without
  fresh permission.
- Do not commit unless the user requests it or explicitly approves the prepared
  commit story.

## Validation Standard

Every implementation change must identify the Milestone 1 acceptance criterion
it satisfies. Prefer fake-device or contract tests before touching hardware.
After hardware testing, record the exact firmware, gateway, OpenClaw, and source
versions used.
