# Instructions for Coding Agents

## Read Order

Before proposing or making changes, read:

1. `README.md`
2. `docs/MILESTONE_3.md`
3. `docs/ARCHITECTURE.md`
4. `docs/MILESTONE_2.md`
5. `docs/MILESTONE_1.md`

Firmware and gateway source are maintained directly under `firmware/` and
`stackchan_mcp/`.

Before changing firmware, also read `firmware/README.md`.

## Current Phase

Milestones 1 and 2 have historical physical acceptance. Milestone 3 is in
progress. Native OpenClaw completion integration is one part of it; the
milestone also covers reproducible deployment, reconnect recovery, continuity,
and restraint. Do not treat completion of the native integration as completion
of Milestone 3.

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
Inspect the current XC Body deployment before changing it. Do not infer host
configuration that has not been inspected.

## Firmware Rules

- Call the product XC Body firmware. Use upstream names only for exact source
  provenance or legacy configuration identifiers.
- Build only the StackChan target with
  `python ./scripts/release.py stackchan` from `firmware/`.
- A generic ESP32-S3 build selects an incompatible board configuration and may
  produce a PSRAM boot loop on CoreS3.
- The 16 MiB layout has an 8 MiB assets partition and two `0x3f0000` app
  partitions.
- Routine app-only flashing uses `xc_body.bin` at `0x20000` and preserves NVS.
  The merged recovery image starts at `0x0` and is not a routine flash image.
- Firmware flashing requires explicit permission for that exact flash.

## Engineering Rules

- Think before coding and state assumptions.
- Prefer the smallest implementation that passes the milestone acceptance
  tests.
- Give each invariant one owner. Trust guarantees already enforced by upstream
  or downstream modules; do not duplicate their validation, ordering, queues,
  retries, or state.
- Add a local guard only at an untrusted boundary or for a proven safety or
  recovery failure. Do not guard every step speculatively.
- Extend the existing `stackchan_mcp` hardware layer before inventing device
  protocols.
- The agent chooses semantic intentions; deterministic code owns expressions,
  timing, LEDs, and servo motion.
- Treat servo safety and reconnect recovery as correctness requirements.
- Before committing a change set under `gateway/` or `stackchan_mcp/`, inspect
  the currently deployed runtime with `scripts/deploy.sh --status` and bump the
  gateway version in `pyproject.toml` from that deployed version. Never derive
  the next version from checked-in source. Firmware versioning remains separate.
- Before committing firmware runtime changes, inspect the published OTA
  manifest and bump `PROJECT_VER` from its firmware version. Never derive the
  next firmware version from checked-in source.
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
