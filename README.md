# XC Body

XC Body gives an existing OpenClaw agent a physical presence through the
official M5Stack StackChan K151/CoreS3 robot.

## North Star

> Make OpenClaw feel physically present at home. StackChan is its persistent,
> expressive body: it remembers, takes initiative at meaningful moments, and
> knows when to remain quiet.

The product is guided by three qualities:

- **Continuity:** the same OpenClaw identity persists across channels and
  physical interactions.
- **Initiative:** the agent may decide that something is worth expressing
  without waiting for a direct command.
- **Restraint:** silence, deferral, and summarization are first-class behavior.

## Current Focus

Milestones 1 and 2 have historical physical acceptance. The current software
focus is **Milestone 3: Continuity and Restraint**: make deployment and recovery
reproducible before adding broader behavior. The recorded physical runs proved
native faces, deterministic head motion, prepared-audio playback, and safe
return to idle, but their full firmware, gateway, OpenClaw, and source version
set was not captured, so they are not a reproducible current deployment claim.

Milestone 2 adds a narrow initiative boundary: classify a background result as
`ignore`, `remember`, or `offer`; offer silently; wait for a deliberate
CoreS3 head pat or head stroke; then play OpenClaw-prepared Opus packets
after acknowledgment. Duplicate playback is suppressed only while a thought ID
remains in bounded recent-ID memory for the running process. Restart or
eviction may replay it; durable restart persistence, quiet hours, microphone
input, camera input, and background-event policy remain out of scope.

The pending-thought boundary has a stdio service and a persistent HTTP
service/proxy, each exposing only `consider_thought`. One process-owned runtime
uses one upstream StackChan MCP session to receive device events. Local
fake-port, full-suite, real-SDK transport, and physical prepared-audio
knock/gesture/playback acceptance tests pass.

## Start Here

A new coding session should read these files in order:

1. [`AGENTS.md`](AGENTS.md)
2. [`docs/HANDOFF.md`](docs/HANDOFF.md)
3. [`docs/MILESTONE_2.md`](docs/MILESTONE_2.md)
4. [`docs/MILESTONE_1.md`](docs/MILESTONE_1.md)
5. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
6. [`docs/DECISIONS.md`](docs/DECISIONS.md)
7. [`docs/ROADMAP.md`](docs/ROADMAP.md)

The tracked machine-readable boundaries are the manual embodiment
[`intent contract`][intent-contract] and the Milestone 2
[`pending-thought contract`][pending-thought-contract].

## Milestone 1 Semantic Bridge

The repository now includes a dependency-free Python 3.10+ semantic core, one
transport-neutral `embody` MCP tool descriptor/handler, and a synchronous
client bridge for the pinned upstream StackChan MCP daemon. The boundary loads
the tracked v1 schema, validates each request, selects an immutable symbolic
recipe, invokes an injected device port, and makes a mandatory
safe-return-to-idle attempt after every expressive intent. A full recipe is
checked for calibration before the first device call.

The opt-in `measured_k151_cores3_calibration()` factory preserves reviewed
motion evidence. Neutral maps to upstream avatar name `idle` and head command
`(0,43,30)`. Curious maps to `thinking` and `(12,50,30)` before returning to
the exact neutral command. Speed `30` represents upstream `low`. Louis confirmed
that the curious pose is clearly visible and appropriately restrained.

Avatar-name mapping is separate from visible-face verification. The measured
factory maps `idle`, `thinking`, `happy`, and `sad`; the reviewed native
320x240 payload and accepted semantic `curious` path use verified `idle` and
`thinking` faces. A successful firmware response alone is still insufficient:
visible verification is bound to the exact reviewed payload and physical run.

The import-safe runner accepts the `curious` shortcut or one v1 semantic intent
JSON object. It accepts no raw face, servo, speed, or return controls. Supported
recipes pass complete local calibration preflight before configuration loading
or MCP session creation. Before device work, both semantic entry points restore
the configured avatar archive and require the exact reviewed SHA-256; another
valid digest fails closed.

Run the dependency-free tests from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

The checks cover deterministic translation, mandatory idle return, rejection
before movement, honest device failures, servo limits, and visible-face
preflight. Real hardware verified the native idle/thinking faces, measured
curious movement, and automatic neutral recovery.

## Candidate Avatar Assets

The repository includes a standard-library-only generator and validator for
a native display-resolution runtime format. It creates 14 complete 320x240
RGB565-LE frames in the required face, eye, and mouth order, plus a JSON
manifest and a labeled PNG contact sheet:

```sh
python3 scripts/build_avatar_assets.py
```

Outputs are deterministic and written beneath the ignored
`build/avatar-assets/` directory. Generation alone is not production
visible-face evidence. The measured calibration's four verified names are valid
only when runtime restoration confirms the exact physically reviewed payload.

The injection-based loader validates the local payload and manifest before
making exactly one upstream `load_avatar_set` call:

```python
from stackchan.avatar_assets import load_validated_avatar_set

load_validated_avatar_set(
    call_tool,
    payload_path="build/avatar-assets/xc-body-layered.rgb565le",
    manifest_path="build/avatar-assets/xc-body-layered.manifest.json",
    archive_path=deployment_archive_path,
)
```

The caller must arrange for `archive_path` to identify those same validated
bytes in the deployment environment. No endpoint, token, or archive path is
provided by the helper. The native format uses the reviewed
`layered-320x240` adaptation stored in
`stackchan/stackchan-mcp-native-avatar.patch`. Firmware renders native frames
at 1x while retaining the upstream 160x120 modes as rollback. Assets must be
reloaded after gateway or device restart unless persistence is proven.

## Repository Layout

```text
contracts/   Versioned contracts between OpenClaw and the physical body
docs/        Product, architecture, milestone, decisions, and handoff material
examples/    Valid example payloads for the current contract
gateway/     Intent validation, orchestration, and safe return
scripts/     Repository checks and deterministic build entry points
stackchan/   Symbolic recipes, calibration, and device adapter
mcp/         Transport-neutral semantic descriptor and handler
references/  Temporary pinned study source; never a runtime dependency
tests/       Responsibility-grouped standard-library tests
```

## Project Boundary

XC Body is separate from `xc-buddy`:

- `xc-body` is the physical home body of an existing OpenClaw agent.
- `xc-buddy` remains the Stick S3 firmware and macOS companion project.

Do not move XC Body work into `xc-buddy`, and do not modify `xc-buddy` while
working here unless the user explicitly requests a cross-project change.

## Status

- GitHub repository: <https://github.com/SiyiLi/xc-body.git>.
- The `stackchan-mcp` gitlink is initialized and pinned to
  `804af573ba8f577f63efbd39f6e8a9c7f57b4647`.
- XC Body uses an isolated service on a separate always-on cloud rendezvous;
  raw gateway ports remain private and credentials are not stored here.
- The app-only native-avatar firmware is flashed with private factory and
  tested rollback artifacts retained.
- OpenClaw has local semantic and pending-thought tool registration; no
  OpenClaw runtime/source patch was made.
- The transport-independent semantic core uses dependency-free Python 3.10+.
  Executable services also require MCP and HTTP/ASGI packages, but this
  repository does not yet pin that complete dependency set or define a
  reviewed service launch unit.
- Each executable service holds one upstream session while connected and exits
  on transport loss. Internal reconnect, supervisor restart behavior, and
  state recovery remain unverified follow-up work.

[intent-contract]: contracts/embodiment-intent.schema.json
[pending-thought-contract]: contracts/pending-thought.schema.json
