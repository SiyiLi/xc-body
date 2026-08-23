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

The complete recovery matrix has physical acceptance with this exact set:

- firmware `0.1.11`, source `3fdbf32`, app SHA-256
  `325b8f2e2f6d7acb6213f03477495c4e00d41bc44dfe19ff572d03997208c2e9`;
- gateway and pending runtime `0.1.5`, deployed source `db6defb`, image digest
  `sha256:f9bdb345d4d8d781cc7d088559f597c76d2ff1722e53e12306282a324b73480c`;
- reviewed avatar SHA-256
  `daa35ed17a716860f0415c053dbf3d59e6e421ad50556de5ccc58cae28af36f7`;
- OpenClaw `2026.7.1-2` with plugin source `3fdbf32`; and
- Caddy `2.11.4`, image digest
  `sha256:542fa24d69f2bb305fcf0201e298bbd8e0cd5a3f35706a664e08e8675619474a`.

The run proved fresh useful operation after an OpenClaw restart, a bounded
OpenClaw-to-VM route failure, a robot reboot, a gateway restart, and a pending
service restart. A pending offer survived robot reconnect without a second
knock. Restarting the pending service forgot its old offer. A real 30-minute
expiry ignored the old touch and accepted a fresh offer. No failed submission
was replayed later. Clear speech followed each accepted fresh touch, and the
control path remained usable after the extended idle period.

Firmware `0.1.11` was delivered by the accepted no-USB OTA path. Earlier
consecutive updates from `0.1.4` through `0.1.6` also installed into inactive
slots, restored the reviewed avatar, and survived validation reboots.

## Remaining Acceptance

OTA acceptance still must prove automatic rollback after an unhealthy boot.
Compatible-image checks and USB recovery are implemented. The intermittent
Si12T I2C crash seen during the first `0.1.3` download did not recur in the
accepted runs but has not been root-caused.
