# Instructions for Coding Agents

## Read Order

Before proposing or making changes, read:

1. `README.md` for current status and the active milestone.
2. `docs/ARCHITECTURE.md` for the current execution model.
3. The active milestone linked from `README.md`.
4. Only the historical milestone files that own behavior being changed.
5. `docs/ROADMAP.md` only when reasoning about future direction.

Firmware and gateway source are maintained directly under `firmware/` and
`stackchan_mcp/`.

Before changing firmware, also read `firmware/README.md`.

## Current Phase

Milestones 1 through 4 have physical acceptance. Milestone 5 implementation is
complete and awaits versioned physical acceptance for expression-aware direct
conversation.

Keep Milestone 5 constrained to:

- seven named expressions plus idle ambient presence selected by projection;
- deterministic face and head recipes with reviewed servo limits;
- replacement of direct attention and the background-offer knock with one
  selected expression before playback or pending wait;
- local touch reactions that create no agent or Telegram traffic;
- USB-only preview and persistence of robot-specific motor calibration;
- exact safe return and existing body-operation serialization; and
- expression-only direct turns when one supported silent expression is a
  natural and complete response; uncertain cases remain conversation.

Do not repeat expression motion during a pending-offer wait, add model-generated
motion parameters, camera input, autonomous semantic moods, constant servo
activity, or more expressions without evidence from real use during Milestone
5. Milestone 6 owns camera-guided ambient presence. USB calibration must not
create a second motion runner or expose raw motor parameters through the
network.

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
- Prefer the smallest implementation that advances the active milestone and
  preserves its reference validation scenarios.
- Simplify the underlying logic before adding protection around it. Do not use
  complex code to protect logic that can be made simpler.
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
- Bump only the first-party release artifact changed by the commit. Do not
  change untouched firmware, gateway, or OpenClaw plugin versions solely to
  align release metadata; derive each changed version from its own published
  or deployed artifact.
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

## Review Rules

- Report a bug only when a normal supported flow gives the user a wrong result.
  Show the trigger, current code path, and visible result.
- Corrupt release assets are fatal. Log the failure and safely return the head;
  do not add fallbacks, retries, or extra validation to keep running.
- USB calibration and conversation are separate. Do not report problems that
  require using both at once unless a normal flow does that.
- Executable code and inspected runtime are the source of truth for current
  behavior. Markdown alone is not evidence; correct it when it disagrees.
- Do not treat theoretical parser or recovery hardening as a feature bug. Add
  it only when the user asks for it or a normal flow proves it is needed.

## Validation Standard

- Keep first-party Python at 88 columns or fewer.
- Keep Markdown prose at 80 columns or fewer. URLs, commands, and diagrams may
  exceed the limit when wrapping would reduce clarity.
- Run the repository line-length check before reporting success.
- Do not build firmware during review or surgical-fix iterations. Build only
  during an explicitly authorized flash or OTA workflow, unless the user
  separately requests a build.
- Every implementation change must identify the active milestone behavior it
  advances.
- Prefer fake-device or contract tests before touching hardware.
- After hardware testing, record the exact firmware, gateway, OpenClaw, and
  source versions used. Historical acceptance without a complete version record
  remains valid evidence, but must not be described as currently reproducible.
