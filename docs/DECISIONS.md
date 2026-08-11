# Decision Log

## D-001: Use a Separate Repository

- **Status:** Accepted
- **Date:** 2026-08-11
- **Decision:** XC Body lives separately from `xc-buddy`.
- **Reason:** StackChan/OpenClaw embodiment has a different device, deployment,
  firmware lineage, and product goal from the Stick S3/macOS companion.

## D-002: Name the Project XC Body

- **Status:** Accepted
- **Date:** 2026-08-11
- **Decision:** Use `xc-body` as the repository name.
- **Reason:** The name is concise and expresses the central relationship:
  OpenClaw is the mind and StackChan is its physical body.

## D-003: Reuse stackchan-mcp First

- **Status:** Accepted for Milestone 1
- **Date:** 2026-08-11
- **Decision:** Pin `kisaragi-mochi/stackchan-mcp` as the initial hardware and
  gateway reference.
- **Reason:** It targets the official K151/CoreS3 kit and already implements
  authenticated connectivity, device status, safe head control, expressions,
  LEDs, speech, listening, camera, and physical events.
- **Consequence:** Do not create a new firmware or device protocol unless a
  concrete missing capability is proven.

## D-004: Treat OpenClaw and the Cloud Rendezvous as Separate Hosts

- **Status:** Accepted fact
- **Date:** 2026-08-11
- **Decision:** OpenClaw and the cloud rendezvous run on separate hosts. The
  rendezvous is always on for Milestone 1.
- **Consequence:** OpenClaw and StackChan each use authenticated outbound
  connections to the VM; the Mac is not assumed to host the device gateway.

## D-005: Begin with Manual Deterministic Embodiment

- **Status:** Accepted
- **Date:** 2026-08-11
- **Decision:** Milestone 1 supports only manual `idle`, `curious`, `pleased`,
  and `concerned` intentions.
- **Reason:** This proves identity, hardware control, safety, and consistency
  before adding autonomy.

## D-006: Keep Physical Recipes out of Model Control

- **Status:** Accepted
- **Date:** 2026-08-11
- **Decision:** OpenClaw selects semantic intentions; reviewed deterministic
  code selects face, LEDs, angles, speed, duration, and return behavior.
- **Reason:** Consistent character behavior and servo safety must not depend on
  generative output.

## D-007: Do Not Select a Runtime Language Yet

- **Status:** Accepted
- **Date:** 2026-08-11
- **Decision:** Add contracts and reference source now, but choose TypeScript,
  Python, or a mixed implementation only after a read-only cloud host inventory
  and inspection of the reuse boundary of `stackchan-mcp`.
- **Reason:** Selecting packaging before knowing the VM's existing service and
  container state would create speculative code.

## D-008: Use a Cloud Rendezvous for Milestone 1

- **Status:** Accepted for Milestone 1
- **Date:** 2026-08-11
- **Decision:** Run an isolated XC Body semantic layer and the `stackchan-mcp`
  shared Streamable HTTP daemon/gateway on the cloud rendezvous host. OpenClaw
  connects outbound and authenticated to the MCP HTTP surface. StackChan
  connects outbound through authenticated WSS over TLS, preferably public TCP
  443 through a reverse proxy.
- **Consequence:** Raw ports 8765, 8766, and 8767 must not be publicly exposed.
  A local LAN/mDNS primary gateway with cloud WSS fallback remains an optional
  future optimization, not the canonical Milestone 1 proof.

## D-009: Preserve Shared-Host Isolation

- **Status:** Accepted
- **Date:** 2026-08-11
- **Decision:** Inventory the cloud rendezvous host read-only before deployment.
  XC Body uses a separate service or container, credentials, storage,
  lifecycle, health checks, resource limits, and reverse-proxy routes.
- **Reason:** The shared VM must not turn deployment or failure of XC Body into
  an availability or security risk for unrelated workloads.

## D-010: Make Safe Return an Embodiment-Layer Invariant

- **Status:** Accepted for Milestone 1
- **Date:** 2026-08-11
- **Decision:** Every expressive recipe returns automatically to `idle` after a
  reviewed bounded duration. `idle` is already idle. The v1 request contract
  exposes no caller control over return behavior or timing.
- **Reason:** Safe servo state and deterministic behavior cannot depend on a
  follow-up model or caller request.
