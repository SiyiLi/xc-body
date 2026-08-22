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

The source implements the scoped plugin, offer lifetime, bounded retries,
robot-session recovery, supervisor recovery, and idle display dimming.

The native completion path has candidate physical evidence from 2026-08-21:
one eligible completion produced one knock, a head touch acknowledged it, and
the resulting speech was clear. That run does not complete the milestone
because the full recovery matrix was not accepted with one exact version set.

The physical no-USB OTA path is accepted. On 2026-08-22, the authenticated
gateway bridge updated `0.1.1` to `0.1.2`. A later physical reset made `0.1.2`
read the stable manifest, download `0.1.3`, write `ota_1`, boot the new slot,
authenticate with the gateway, and mark the image valid. The accepted `0.1.3`
app SHA-256 is
`2c2f21466f6ba00b88e37f342483d6983948fccb499fe1bac77b39d61b8da144`.

The first `0.1.3` download attempt crashed at two percent in the existing
Si12T head-touch I2C poll. The incomplete slot was not activated; the robot
rebooted as `0.1.2`, automatically retried the manifest, and completed the
update. The robot now runs valid firmware `0.1.3` on `ota_1` with gateway
runtime `0.1.2` and Caddy `2.11.4`. The gateway restored an initialized
41-tool session and verified transfer of the reviewed layered avatar. The user
confirmed that the restored idle avatar was visible. OpenClaw was not part of
this OTA test.

The physical run used gateway runtime `0.1.2`. Current gateway source is
`0.1.3`, with bounded MCP request deadlines, but it has not been deployed or
physically accepted. The complete run therefore remains unreproduced with one
exact current version set.

## Remaining Acceptance

Run one versioned physical matrix proving fresh completions after:

1. an OpenClaw restart;
2. a temporary OpenClaw-to-VM interruption;
3. a robot reboot;
4. a VM gateway restart; and
5. a pending-service restart.

The same run must prove that an offer survives robot reconnect, expires after
30 minutes, never creates a delayed burst, and that idle display dimming keeps
the authenticated control transport usable.

OTA acceptance still must prove automatic rollback after an unhealthy boot.
The compatible-image checks and USB recovery remain implemented but have not
been exercised as destructive physical tests. The intermittent Si12T I2C
crash seen during the first `0.1.3` download must also be resolved or shown not
to recur.
