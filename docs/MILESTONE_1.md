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

See `contracts/embodiment-intent.schema.json` for the machine-readable contract.

## Initial Physical Vocabulary

| Intention | Face | Movement | Milestone 1 speech |
| --- | --- | --- | --- |
| `idle` | Neutral | Relaxed calibrated center | None |
| `curious` | Thinking or attentive | Small upward tilt and restrained side glance | Disabled initially |
| `pleased` | Happy | One small nod | Disabled initially |
| `concerned` | Sad or concerned | Small head tilt followed by stillness | Disabled initially |

Exact angles, speeds, durations, and face assets must be chosen only after
observing the real hardware. They must remain inside upstream safety limits and
must not use large abrupt reversals.

## Prepared Execution Order

No step below is authorized merely by being documented.

1. Inspect the OpenClaw host: version, launch method, MCP support, and
   configuration boundary.
2. Perform a read-only inventory of the cloud rendezvous host, including its
   existing workload isolation, service manager or container runtime, reverse
   proxy, TLS termination, routes, firewall, health checks, and resource
   headroom. Do not disturb unrelated workloads.
3. Record the StackChan model, installed firmware version, official recovery
   path, and whether official-app compatibility must be preserved.
4. Inspect the pinned `stackchan-mcp` revision and choose the least invasive
   firmware path.
5. Run gateway and contract tests without hardware.
6. Establish authenticated MCP Streamable HTTP and WSS connectivity through
   the isolated XC Body deployment.
7. Verify low-level device health, face switching, and small safe head motion.
8. Calibrate and record the neutral pose, bounded durations, and physical
   recipes.
9. Connect the semantic embodiment tools to OpenClaw.
10. Add the minimal OpenClaw guidance required to select semantic intentions.
11. Run every acceptance test and record exact versions.

## Acceptance Tests

1. A manual OpenClaw request for `curious` produces the reviewed curious recipe.
2. `pleased` produces exactly one small nod and the reviewed happy expression.
3. `concerned` produces the reviewed restrained concerned recipe.
4. Every expressive recipe returns to `idle` automatically after its reviewed
   bounded duration; no high-level request can suppress or reschedule the
   return.
5. Repeating a request produces the same behavior rather than model-generated
   variation.
6. An unsupported intent produces no physical movement and a clear error.
7. OpenClaw receives an honest failure when StackChan is unavailable.
8. A connection loss during a behavior does not leave a servo repeatedly driven.
9. Resetting StackChan restores connectivity without reflashing.
10. Restarting the gateway restores connectivity without device reconfiguration.
11. No high-level request can bypass reviewed servo limits.
12. The stock firmware recovery path remains documented and usable.

## Exit Criteria

Milestone 1 is complete only when all acceptance tests pass on the user's actual
StackChan and the exact tested versions are recorded in `docs/HANDOFF.md` or a
dedicated hardware evidence document.

Speech may be added at the end of Milestone 1 only after silent expression and
recovery are reliable. It is not required for milestone completion.
