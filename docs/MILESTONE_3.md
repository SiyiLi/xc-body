# Milestone 3: Continuity and Restraint

## Objective

Make XC Body return to useful operation automatically after ordinary network,
robot, and service interruptions without adding persistent queues or policy.

## Scope

Milestone 3 includes:

- Native OpenClaw integration for selected successful background completions.
- One in-memory offer that expires 30 minutes after its knock completes.
- Quiet rejection of new offers while one is pending.
- Bounded OpenClaw-to-VM retries without a delayed queue.
- Automatic reviewed-avatar restoration after a robot reconnect while keeping
  a still-valid in-process offer.
- Supervisor restart recovery that forgets old process state and accepts the
  next fresh completion.
- Connected idle display dimming without dropping the control transport.
- Reproducible deployment and physical acceptance of the integrated path.

No persistence database, durable queue, quiet hours, cooldowns, quotas, or
background replay are part of this milestone.

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

Milestone 3 is complete when fresh real OpenClaw completions work after an
OpenClaw restart, a temporary Mac-to-VM interruption, a robot reboot, a VM
gateway restart, and a pending-service restart. A pending offer must survive a
robot reconnect, expire after 30 minutes, and never produce a delayed burst.
The idle display must dim while the authenticated control transport remains
usable. Final acceptance uses exact source, image, firmware, and OpenClaw
versions.
