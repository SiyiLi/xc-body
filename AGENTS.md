# Instructions for Coding Agents

## Read Order

Before proposing or making changes, read:

1. `README.md`
2. `docs/HANDOFF.md`
3. `docs/MILESTONE_2.md`
4. `docs/MILESTONE_1.md`
5. `docs/ARCHITECTURE.md`
6. `docs/DECISIONS.md`
7. `docs/ROADMAP.md`

Inspect `references/stackchan-mcp` only for the specific capability being
implemented. It is a pinned reference dependency, not a place for casual edits.

## Current Phase

Milestones 1 and 2 have historical physical acceptance. The current software
focus is Milestone 3 planning: reproducible deployment, reconnect recovery, and
continuity. This combined Milestone 2 commit also carries two intentional
prerequisites: the persistent semantic service hardens serialized safe return,
and the calibrated curious hold preserves the physically reviewed interaction.

Keep the completed Milestone 2 scope limited to:

- `ignore`, `remember`, and `offer` decisions.
- One pending offer at a time.
- A silent deterministic knock that reveals no prepared audio.
- Deliberate CoreS3 head-pat or head-stroke acknowledgment.
- OpenClaw-prepared Opus playback after acknowledgment.
- Duplicate suppression for retained recent IDs within the running process.

Do not add restart persistence, quiet hours, cooldowns, background-event policy,
microphone or camera input, free-form motion, Home Assistant, Stick S3
integration, or a mobile app during Milestone 2.

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

- Keep first-party Python at 88 columns or fewer.
- Keep Markdown prose at 80 columns or fewer. URLs, commands, and diagrams may
  exceed the limit when wrapping would reduce clarity.
- Run the repository line-length check before reporting success.
- Every implementation change must identify the active milestone acceptance
  criterion it satisfies.
- Prefer fake-device or contract tests before touching hardware.
- After hardware testing, record the exact firmware, gateway, OpenClaw, and
  source versions used. Historical acceptance without a complete version record
  remains valid evidence, but must not be described as currently reproducible.
