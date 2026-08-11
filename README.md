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
[`contracts/embodiment-intent.schema.json`](contracts/embodiment-intent.schema.json).

## Repository Layout

```text
contracts/   Versioned contracts between OpenClaw and the physical body
docs/        Product, architecture, milestone, decisions, and handoff material
examples/    Valid example payloads for the current contract
references/  Pinned upstream source used for study and reuse
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
- Milestone 1 uses a separate always-on cloud rendezvous host, subject to a
  read-only inventory before deployment.
- No firmware has been flashed for this project.
- Runtime language and packaging remain intentionally undecided until the VM
  inventory and pinned upstream reuse boundary are inspected.
