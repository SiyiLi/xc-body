# Milestone 3: Continuity and Restraint

## Objective

Return XC Body to useful operation after ordinary OpenClaw, network, robot,
gateway, and service interruptions without adding durable queues or policy.

## Scope

- Native OpenClaw integration for selected successful background completions.
- One in-memory offer that expires 30 minutes after its knock completes.
- Quiet rejection of new offers while one is pending.
- Bounded submission retries without delayed replay.
- Reviewed-avatar restoration after a robot reconnect while preserving a
  still-valid in-process offer.
- Supervisor recovery that forgets process state and accepts fresh work.
- Connected idle display dimming without dropping the control transport.
- Safe dual-slot firmware OTA with automatic rollback and USB recovery.
- Reproducible deployment and exact physical acceptance.

Persistence, durable queues, quiet hours, cooldowns, quotas, background replay,
camera input, microphone input, and free-form motion are out of scope.

## Current Status

The complete recovery matrix has historical physical acceptance. The run
proved fresh useful operation after an OpenClaw restart, a bounded
OpenClaw-to-VM route failure, a robot reboot, a gateway restart, and a pending
service restart. A pending offer survived robot reconnect without a second
knock. Restarting the pending service forgot its old offer. A real 30-minute
expiry ignored the old touch and accepted a fresh offer. No failed submission
was replayed later. Clear speech followed each accepted fresh touch, and the
control path remained usable after the extended idle period.

The accepted no-USB OTA path installed consecutive updates into inactive
slots, restored the reviewed avatar, and survived validation reboots.

Rollback safety also has physical acceptance. An unhealthy OTA image panicked
before the MCP health gate completed. The bootloader restored the prior image
and disabled automatic OTA, preventing an update loop.

The intermittent Si12T I2C crash seen during an early download did not recur in
the accepted runs but has not been root-caused.
