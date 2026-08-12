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

The repository is in **Milestone 1: manual embodiment**. The only objective is
to prove that an existing OpenClaw agent can deliberately present a small,
deterministic set of intentions through the physical StackChan.

Milestone 1 does not include cron jobs, autonomous behavior, continuity state,
touch input, microphone, camera, Stick S3 integration, or custom firmware unless
the existing hardware layer proves insufficient.

## Start Here

A new coding session should read these files in order:

1. [`AGENTS.md`](AGENTS.md)
2. [`docs/HANDOFF.md`](docs/HANDOFF.md)
3. [`docs/MILESTONE_1.md`](docs/MILESTONE_1.md)
4. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
5. [`docs/DECISIONS.md`](docs/DECISIONS.md)
6. [`docs/ROADMAP.md`](docs/ROADMAP.md)

The initial machine-readable command boundary is
[`contracts/embodiment-intent.schema.json`][intent-contract].

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
`(0,43,30)`. Curious maps to `thinking` and `(3,45,30)` before returning to the
exact neutral command. Speed `30` represents upstream `low`. Head movement was
visually confirmed; the curious pose reached `(1,44)` before settling at
`(0,43)`.

Avatar-name mapping is separate from visible-face verification. The measured
factory maps `idle`, `thinking`, `happy`, and `sad`, but verifies none as
visible. The pinned source and `firmware-v1.16.0` tag use placeholder 1x1 black
assets, and real hardware showed no display change. A successful firmware
response therefore proves only that LVGL accepted an asset, not that a person
could see an expression.

The import-safe runner accepts the `curious` shortcut or one v1 semantic intent
JSON object. It accepts no raw face, servo, speed, or return controls. With the
current measured calibration, every semantic intent fails during local recipe
preflight, before configuration loading or MCP session creation. Raw/manual
head checks remain outside this semantic success path.

Run the dependency-free tests from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

The checks cover deterministic translation, mandatory idle return, rejection
before movement, honest device failures, servo limits, and visible-face
preflight. Real hardware has verified measured head movement and neutral
recovery, but display rendering failed. Full acceptance criteria 1-4 remain
blocked until real assets are installed and their face changes are confirmed
by human observation.

## Repository Layout

```text
contracts/   Versioned contracts between OpenClaw and the physical body
docs/        Product, architecture, milestone, decisions, and handoff material
examples/    Valid example payloads for the current contract
gateway/     Intent validation, orchestration, and safe return
stackchan/   Symbolic recipes, calibration, and device adapter
mcp/         Semantic descriptor and handler; no server yet
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
- The `stackchan-mcp` gitlink is pinned to
  `804af573ba8f577f63efbd39f6e8a9c7f57b4647`; the local submodule is currently
  uninitialized.
- Milestone 1 uses a separate always-on cloud rendezvous host. Read-only
  discovery is complete; deployment, TLS routing, secrets, firewall changes,
  and OpenClaw MCP configuration remain unapproved and unapplied.
- No firmware has been flashed for this project.
- The transport-independent semantic core uses dependency-free Python 3.10+.
  Deployment packaging remains intentionally undecided until the host inventory
  and pinned upstream reuse boundary are inspected.

[intent-contract]: contracts/embodiment-intent.schema.json
