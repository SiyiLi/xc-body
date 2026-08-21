# Milestone 3: Continuity and Restraint

## Objective

Make XC Body's initiative path a durable, restrained part of OpenClaw rather
than a manually initiated or process-local demonstration.

## Scope

Milestone 3 includes:

- Native OpenClaw integration for selected successful background completions.
- Durable pending-thought and duplicate state across process restarts.
- Defined reconnect recovery with authoritative session ownership.
- Quiet hours, cooldowns, and bounded offer policy.
- Reproducible deployment and physical acceptance of the integrated path.

The native OpenClaw integration is one part of this milestone. It does not by
itself satisfy the continuity, recovery, or restraint requirements.

## Native OpenClaw Integration Part

The current implementation observes successful subagent and cron completions,
uses OpenClaw's native LLM runtime to choose `offer` or `skip`, and sends an
accepted Chinese summary to the authenticated VM endpoint. The VM prepares and
validates audio before entering the Milestone 2 knock, wait, touch, and playback
state machine. Ordinary interactive turns are excluded.

Implementation and integration tests pass. A candidate deployment completed
Gateway configuration and restart, the live VM path, and physical acceptance
on 2026-08-21: one successful completion was classified and submitted, the
robot knocked once, a head touch acknowledged the offer, and speech was clear.

## Milestone Exit Criteria

Milestone 3 is complete only when all parts above are implemented and verified,
including restart continuity, reconnect behavior, restraint policy, and a fresh
physical end-to-end acceptance. Passing the native OpenClaw integration tests
alone is not milestone completion.
