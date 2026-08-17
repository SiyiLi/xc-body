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
  authenticated connectivity, device status, safe head control, avatar-name
  selection, LEDs, speech, listening, camera, and physical events. Bundled
  avatar assets still require separate visible-render verification.
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

## D-011: Use Python for the Transport-Independent Semantic Core

- **Status:** Accepted for Milestone 1
- **Date:** 2026-08-11
- **Decision:** Implement the pure semantic core in dependency-free Python
  3.10+, matching the pinned upstream gateway's language floor.
- **Consequence:** This narrows D-007 only for the core. Cloud deployment
  packaging remains undecided until the host inventory and gateway integration
  boundary are inspected.

## D-012: Keep v1 Speech Explicitly Disabled

- **Status:** Accepted for Milestone 1
- **Date:** 2026-08-11
- **Decision:** The optional v1 `speech` property accepts only JSON `null`.
  Omission and `null` are equivalent; non-null values fail validation before
  any device call.
- **Reason:** A null-only compatibility field keeps the example stable without
  advertising speech that this milestone slice cannot execute.

## D-013: Reuse the Pinned Shared Daemon Surface

- **Status:** Accepted for Milestone 1
- **Date:** 2026-08-11
- **Decision:** Adapt the semantic embodiment layer to the pinned
  `stackchan-mcp` `0.17.0` shared Streamable HTTP daemon rather than creating a
  second gateway or device protocol.
- **Evidence:** Exact-revision inspection confirmed loopback `/mcp`, bearer and
  allowed-host validation, authenticated ESP32 WSS, bounded device-command
  serialization, and health/status endpoints.
- **Consequence:** OpenClaw sees a semantic-only tool surface. Raw head, face,
  and LED controls remain behind the XC Body adapter.

## D-014: Keep Deployment Changes Behind a Separate Approval Gate

- **Status:** Accepted for Milestone 1
- **Date:** 2026-08-11
- **Decision:** Read-only discovery may inform code and documentation, but
  creating the service/container, public TLS route, firewall changes, secrets,
  or OpenClaw MCP configuration requires separate explicit authorization.
- **Reason:** The rendezvous host carries unrelated workloads, and discovery
  found no existing public XC Body route that can be safely assumed or reused.

## D-015: Opt In to Partial Measured Calibration

- **Status:** Accepted for the prepared real E2E slice
- **Date:** 2026-08-11
- **Decision:** Encode measured K151/CoreS3 values in an explicit factory. Idle
  maps to `idle` and `(0,43,30)`; curious maps to `thinking` and `(12,50,30)`
  before returning to idle. Speed `30` represents upstream `low`. The factory
  records no visibly verified faces.
- **Reason:** Deployment must deliberately choose motion calibration and
  visible-face evidence, and recipe completeness must be checked before device
  work.
- **Consequence:** All production semantic intentions fail with zero upstream
  calls until their mapped face assets are installed and visibly verified.
  `Pleased` and `concerned` also require measured and reviewed motions.

## D-016: Inject the Synchronous Upstream Tool Boundary

- **Status:** Accepted for Milestone 1
- **Date:** 2026-08-11
- **Decision:** Implement `StackChanClient` through an injected synchronous MCP
  tool callable. The runnable cloud boundary owns the MCP SDK session and async
  coordination; semantic orchestration remains synchronous.
- **Reason:** Result translation and device sequencing stay fake-testable while
  the cloud process can reuse the pinned daemon without another protocol.

## D-017: Reject Command Success as Visible-Render Evidence

- **Status:** Accepted correction
- **Date:** 2026-08-11
- **Decision:** Do not accept a semantic expression from command response alone.
  Require physical state evidence; face changes require human-visible
  confirmation without adding camera or vision scope.
- **Evidence:** A real `curious` run moved the head and recovered neutral, but
  produced no visible display change. The pinned revision and
  `firmware-v1.16.0` tag contain 1x1 black placeholder avatar assets. Firmware
  success only confirmed that LVGL applied the selected asset.
- **Consequence:** Head motion measurements remain calibration evidence, but
  full criteria 1-4 are blocked until real face assets are installed and
  visibly verified.

## D-018: Prepare Layered Runtime Assets Without Granting Verification

- **Status:** Accepted for Milestone 1 preparation
- **Date:** 2026-08-11
- **Decision:** Generate an exact 14-frame native 320x240 RGB565-LE payload
  with dependency-free deterministic code. Bind it to a manifest and validate
  it before any injected upstream `load_avatar_set` call.
- **Reason:** Native panel resolution removes the upstream 160x120-to-320x240
  scaling compromise while keeping artwork reproducible and reviewable.
- **Consequence:** Generated previews and hashes are not human-visible evidence.
  Production `verified_faces` stays empty until Louis holds and directly
  confirms each runtime-loaded face. Reload after restart remains required
  unless persistence is separately proven.

## D-019: Require Consent Before Telling a Background Result

- **Status:** Accepted for Milestone 2
- **Date:** 2026-08-12
- **Decision:** Classify each background result as `ignore`, `remember`, or
  `offer`. An offer may knock silently, but XC Body plays OpenClaw-prepared
  Opus packets only after `touch/tap/head_pat` or
  `touch/stroke/head_stroke` acknowledges it.
- **Reason:** Initiative should create an opportunity for interaction, not an
  unsolicited announcement into the room.
- **Consequence:** One offer may wait at a time. Prepared audio is never
  passed to the knock boundary. The tell boundary uses `thought_id` to suppress
  duplicates while that ID remains in bounded memory for the running process;
  restart or eviction may replay it. No text-to-`say` path exists. Restart
  persistence, batching, quiet hours, and selection policy remain Milestone 3
  or later work.
