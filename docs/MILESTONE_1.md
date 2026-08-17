# Milestone 1: OpenClaw Gets a Body

## Objective

Prove that an existing OpenClaw agent can manually request a semantic intention
and have the physical StackChan present it consistently and safely.

This milestone is successful when the interaction feels like OpenClaw
deliberately inhabiting StackChan, even though it is not yet autonomous.

## In Scope

- Inspect the OpenClaw runtime and supported integration surface.
- Establish the accepted authenticated path through the cloud rendezvous host
  to StackChan.
- Reuse the pinned `stackchan-mcp` firmware and gateway when feasible.
- Expose four semantic intentions: `idle`, `curious`, `pleased`, `concerned`.
- Define deterministic physical recipes for the three expressive intentions.
- Return safely and smoothly to idle.
- Report device availability and command failure honestly.
- Verify reconnect behavior after device and gateway restarts.

## Out of Scope

- Cron jobs and autonomous initiation.
- Persistent pending thoughts or cross-session continuity.
- Touch events flowing back to OpenClaw.
- Speech recognition, microphone capture, camera, or face recognition.
- Ambient personality, circadian behavior, or random idle motion.
- Home Assistant and household automation.
- Stick S3 or XC Buddy integration.
- Custom firmware unless a required capability is missing or broken upstream.
- Custom avatar artwork beyond what is necessary to distinguish expressions.

## Intent Contract

The OpenClaw-facing boundary is semantic:

```json
{
  "version": "v1",
  "intent": "curious",
  "speech": null
}
```

OpenClaw must not generate raw servo angles or arbitrary LED animations. The
embodiment layer maps each supported intention to a reviewed physical recipe.
Every expressive recipe returns to `idle` under embodiment-layer control after
a reviewed bounded duration. `idle` is already idle, and no high-level caller
can disable safe return or choose its timing.

For the coherent v1 Milestone 1 API, `speech` is optional but may only be JSON
`null`. Omitting it and supplying `null` are equivalent. Any non-null value is
rejected before device execution because speech is disabled in this slice.

See `contracts/embodiment-intent.schema.json` for the machine-readable contract.

## Initial Physical Vocabulary

| Intention | Face mapping | Movement | Milestone 1 speech |
| --- | --- | --- | --- |
| `idle` | `idle`; visibly verified | `(0,43,30)` | None |
| `curious` | `thinking`; visibly verified | `(12,50,30)`, then idle | Disabled |
| `pleased` | `happy`; visibly verified | Blocked: not calibrated | Disabled |
| `concerned` | `sad`; visibly verified | Blocked: not calibrated | Disabled |

The reviewed curious command is yaw `12`, pitch `50`, speed `30`, held for ten
seconds before exact neutral yaw `0`, pitch `43`. Speed `30` represents upstream
`low`. New values must remain inside upstream yaw `-90..90` and pitch `5..85`
limits and must not use large abrupt reversals.

## Current Code and Discovery Boundary

The dependency-free semantic core now preflights the complete requested recipe,
executes against an injected device port, and attempts idle in `finally` after
each expressive request. If expression and idle both fail, both failures are
preserved. Calibration records upstream avatar-name mappings separately from
human-visible face verification. Any unverified face rejects the full recipe
before a client call. An explicit measured factory preserves idle and curious
motion and marks `idle`, `thinking`, `happy`, and `sad` visibly verified for the
exact reviewed native payload. The adapter enforces upstream servo limits and
leaves pleased and concerned motion incomplete.

An injected synchronous wrapper translates the pinned daemon's MCP tool result
shapes into the existing `StackChanClient` protocol. The import-safe cloud
runner loads its URL and bearer token only from arguments/environment, opens an
MCP SDK session, and runs synchronous embodiment in a worker thread. It returns
only a machine-readable semantic result. No MCP SDK is imported at module load.
A first real `curious` call exposed the pinned firmware's 1x1 black placeholder
avatars. The later reviewed native-avatar adaptation loaded full 320x240 faces;
human observation confirmed the mapped faces, curious movement, and exact
neutral recovery.

An offline, dependency-free pipeline now prepares a candidate replacement set
for the native display-resolution runtime adaptation. It generates 14 complete
320x240 RGB565-LE frames in the required order, validates total and per-frame
hashes, enforces meaningful pairwise differences across the six face frames,
and produces a labeled PNG contact sheet. Eye and mouth states are complete
idle-style faces because layered frames replace the full display frame.

The generator validates the local manifest and payload before writing them. At
startup, each semantic entry point loads the deployment archive through adapted
upstream `load_avatar_set` using `layered-320x240` and requires the
device-reported checksum to match the reviewed payload. Verification is bound
to that device session; a session change fails closed before body action.
Native support requires the reviewed gateway change and a firmware flash;
legacy 160x120 modes remain available for rollback. Runtime loading is not
assumed to persist across restart.

Fake and contract checks cover deterministic sequencing, error preservation,
servo limits, and fail-closed visible-face preflight. Historical hardware
evidence covers reviewed native faces, curious movement, and neutral recovery.
Pleased and concerned remain disabled because their motions are not calibrated;
restart recovery still requires a fully versioned acceptance run.

Read-only discovery is complete for the OpenClaw MCP client surface, the shared
rendezvous constraints, and pinned `stackchan-mcp` `0.17.0`. The evidence
supports an isolated cloud service using remote Streamable HTTP MCP plus an
authenticated outbound device WSS connection. Later explicitly authorized work
deployed the isolated service and app-only firmware; public route and service
reproduction details remain incomplete.

## Prepared Execution Order

No step below is authorized merely by being documented.

1. Record the StackChan model, installed firmware version, official recovery
   path, and whether official-app compatibility must be preserved.
2. Add and test the semantic-core adapter to the pinned `stackchan-mcp` daemon
   surface using a fake transport; keep raw controls private.
3. Prepare the isolated service/container and public TLS route for review. Do
   not modify the rendezvous or expose raw ports without explicit permission.
4. Prepare the minimal OpenClaw remote MCP definition, authentication header,
   TLS settings, timeouts, and semantic-only tool allowlist for review.
5. With explicit deployment/config authorization, establish authenticated MCP
   Streamable HTTP and WSS connectivity.
6. Verify low-level device health and small safe head motion. Raw/manual head
   checks do not count as semantic success.
7. With explicit deployment and hardware permission, runtime-load the reviewed
   asset payload. A flash is not required for this upstream path.
8. Hold each enabled face for human observation. Record the exact payload hash
   and update `verified_faces` only for faces that are visibly confirmed.
9. Calibrate and record the neutral pose, bounded durations, and physical
   recipes.
10. Connect the semantic embodiment tools to OpenClaw.
11. Add the minimal OpenClaw guidance required to select semantic intentions.
12. Run every acceptance test and record exact versions. Include runtime asset
    reload checks after both gateway and device restart.

## Acceptance Tests

1. A manual OpenClaw request for `curious` produces the reviewed curious recipe,
   including a human-visible attentive face.
2. `pleased` produces exactly one small nod and the reviewed happy expression.
3. `concerned` produces the reviewed restrained concerned recipe.
4. Every expressive recipe returns to a human-visible `idle` automatically
   after its reviewed bounded duration; no high-level request can suppress or
   reschedule the return.
5. Repeating a request produces the same behavior rather than model-generated
   variation.
6. An unsupported intent produces no physical movement and a clear error.
7. OpenClaw receives an honest failure when StackChan is unavailable.
8. A connection loss during a behavior does not leave a servo repeatedly driven.
9. Resetting StackChan restores connectivity without reflashing.
10. Restarting the gateway restores connectivity without device reconfiguration.
11. No high-level request can bypass reviewed servo limits.
12. The stock firmware recovery path remains documented and usable.
13. A successful expression has both a successful command response and physical
    state evidence. Face changes require human-visible confirmation; no camera
    or vision system is required.

## Exit Criteria

Milestone 1 is complete only when all acceptance tests pass on the user's actual
StackChan and the exact tested versions are recorded in `docs/HANDOFF.md` or a
dedicated hardware evidence document.

Speech may be added at the end of Milestone 1 only after silent expression and
recovery are reliable. It is not required for milestone completion.
