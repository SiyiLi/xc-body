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

The physical no-USB OTA path is accepted. On 2026-08-22, the robot completed
two consecutive boot updates: `0.1.4` to `0.1.5`, then `0.1.5` to `0.1.6`.
Each image installed in the inactive slot, authenticated with the gateway,
completed MCP initialization, restored the reviewed layered avatar, and
survived a validation reboot without rollback. Configuration mode advertised
the reviewed `XCBODY-3341` SSID on `0.1.6`.

The robot now runs firmware `0.1.7`. The last fully recorded acceptance set
used firmware `0.1.6` on `ota_0`, gateway runtime `0.1.2`, and Caddy `2.11.4`.
Its accepted app SHA-256 is
`3265a8e84bd306c7f705792ed1370e352fd6cca0f3da140f8765588fa9a5e2b9`.
OpenClaw was not part of those OTA tests.

Firmware records a pending target before switching slots. If the bootloader
rolls back, the recovered slot records the failed version and disables
automatic boot OTA until local USB or configuration-screen control clears the
block. Those local controls are physically exercised; forced unhealthy-boot
rollback remains untested.

The physical run used gateway runtime `0.1.2`. Current gateway source is
`0.1.4`, with bounded MCP request deadlines and prepared-audio caching, but it
has not been deployed or physically accepted. The complete run therefore
remains unreproduced with one exact current version set.

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
