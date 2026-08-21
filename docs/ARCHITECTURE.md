# Architecture

## Milestone 3 native completion offer path

This path is one part of Milestone 3. It automates selection and delivery of
completion offers; it does not by itself complete Milestone 3's continuity and
restraint scope.

`openclaw-plugin/` subscribes to typed `subagent_ended` and `cron_changed`
hooks. Successful runs are deduplicated operationally, classified through
`api.runtime.llm.complete`, and skipped or submitted as a versioned Chinese
summary over authenticated TLS. No interactive-turn hook is present.

The VM `/summary/v1` boundary keeps plaintext in request scope, synthesizes and
validates prepared Opus, then passes only the existing prepared-audio offer
contract to the pending-thought runtime. Robot behavior remains the accepted
silent knock, consent wait, touch acknowledgement, and playback flow.


## Current Deployment Fact

OpenClaw and StackChan run on separate hosts and connect outbound to an
always-on cloud rendezvous. The rendezvous host may also run unrelated
workloads, which must remain isolated from XC Body.

A read-only inventory confirmed that the rendezvous host has Docker and enough
headroom for a separately isolated XC Body service. Existing workloads and
ports must remain untouched. Tencent blocks the unfiled `body.siyi.ai` domain,
so the public device route is instead `wss://43.143.37.91`. An isolated Caddy
proxy obtains and renews a short-lived public IP certificate; raw gateway ports
remain private.

The transport-independent semantic core is dependency-free Python 3.10+. The
deployment builds a `linux/amd64` runtime image from either a clean XC Body
commit or an explicitly authorized candidate working tree, plus the reviewed
avatar. It publishes that image and the Caddy image to TC Artifactory; the VM
pulls their exact digests and retains secrets only in its existing private
environment.

## Preferred Milestone 1 Shape

```text
OpenClaw host                            Cloud rendezvous host
┌──────────────┐  outbound authenticated  ┌───────────────────────────────┐
│ OpenClaw     │──MCP Streamable HTTP/TLS─▶│ XC Body reverse-proxy route   │
└──────────────┘                           │ ┌───────────────────────────┐ │
                                           │ │ isolated XC Body service  │ │
official StackChan K151/CoreS3             │ │ - semantic embodiment     │ │
┌─────────────────────────────┐ outbound   │ │ - stackchan-mcp shared    │ │
│ stackchan-mcp firmware      │──auth WSS─▶│ │   HTTP daemon/gateway     │ │
└─────────────────────────────┘ over TLS   │ └───────────────────────────┘ │
                                           ├───────────────────────────────┤
                                           │ isolated unrelated workloads │
                                           └───────────────────────────────┘
```

OpenClaw's managed MCP registry supports remote Streamable HTTP servers with
headers, TLS verification, timeouts, and tool filtering, so it can connect
outbound and authenticated to the XC Body MCP HTTP surface without an OpenClaw
runtime patch. StackChan also initiates its authenticated WSS connection
outbound, so the home router needs no inbound device route. The deployed route
terminates public TLS on TCP 443. Raw gateway ports 8765, 8766, and 8767 must
not be publicly exposed.

Cloud-only through the rendezvous is the simplest canonical Milestone 1 proof
unless inventory evidence changes the choice. In this milestone, "anywhere"
means any usable Wi-Fi network that permits outbound Internet access; captive
portals and restrictive networks remain limitations.

## Optional Future/Home Optimization

```text
StackChan ──local Wi-Fi/mDNS──▶ local gateway (primary)
    └────────authenticated WSS/TLS──▶ cloud gateway (fallback)
```

This may reduce latency at home while preserving the cloud path away from home
or when local discovery fails. It is not part of the canonical Milestone 1
proof and should not be implemented speculatively.

## Component Responsibilities

### OpenClaw

- Owns agent identity and reasoning.
- Selects semantic intentions.
- For an offer, supplies the exact short sentence that may be spoken aloud.
- Does not own servo angles, timing, or animation details.

### XC Body embodiment layer

- Validates the versioned intent contract.
- Maps supported intentions to deterministic physical recipes.
- Returns every expressive recipe to `idle` after its reviewed bounded duration;
  high-level callers cannot disable or reschedule that return.
- Treats an `idle` request as already idle.
- Rejects unsupported or unsafe requests.
- Reports real device success or failure.
- Later owns continuity and restraint state, but not during Milestone 1.

The implemented slice includes an executable semantic `embody` service, an
injected synchronous device port, a fail-closed adapter, and an upstream MCP
client wrapper. The cloud runner confines MCP SDK async behavior to its session
boundary and executes the synchronous semantic path in a worker thread. URL
and bearer token values have no code defaults. Importing the runner starts no
session and performs no environment reads.

The long-lived semantic service admits one complete recipe at a time,
including its bounded hold and mandatory idle return. Cancellation while
queued causes no body work. Once a recipe starts, caller cancellation is
reported only after that recipe finishes its idle attempt, and service shutdown
drains active recipe work before closing the upstream session.

Calibration remains explicit and immutable. Upstream avatar-name mapping and
human-visible face verification are separate facts. A complete recipe,
including mandatory idle return, resolves both facts before device work. An
unverified face raises a typed visible-face verification error with zero client
calls. The runner applies the measured calibration preflight before endpoint
configuration or MCP session creation.

The reviewed deployment factory preserves measured idle and curious motion and
maps four visibly verified upstream avatar names. That verification is
conditional on the exact reviewed native payload: each semantic process restores
the configured archive and compares the returned checksum with the recorded
SHA-256 before accepting requests. Pleased and concerned motion remains
incomplete. Servo commands are checked against upstream yaw `-90..90` and pitch
`5..85` limits at calibration construction. Historical hardware acceptance
confirmed the faces, curious movement, and exact neutral recovery; incomplete
version capture means that run is not a reproducible current deployment claim.

Candidate runtime artwork is a separate, dependency-free preparation boundary.
`stackchan/avatar_assets.py` deterministically produces and validates the exact
14-frame layered payload: six face frames, three complete eye-state frames,
and five complete mouth-state frames. Every frame is native 320x240
RGB565-LE. The manifest binds exact ordering, offsets, total size, payload
SHA-256, and
per-frame SHA-256 values. Validation also rejects primary faces that differ by
only a cosmetic pixel count.

The generator validates local bytes and their manifest before writing them.
At startup, each service calls upstream `load_avatar_set` with
`mode="layered-320x240"`. Semantic readiness requires the device result
checksum to equal the reviewed payload digest, not merely a valid digest. The
gateway and firmware implement that mode directly, select LVGL 1x scaling for
native descriptors, and keep legacy 160x120 modes unchanged. The caller is
responsible for
making the exact validated bytes available at the deployment archive path.

### Milestone 2 initiative boundary

Milestone 2 keeps judgment and policy in OpenClaw while adding one strict,
transport-neutral pending-thought state machine under `gateway/`. A background
result enters through the tracked `pending-thought.v1` contract and becomes
`ignored`, `remembered`, or `waiting`.

Only `waiting` calls the injected knock port. That call receives only the
opaque `thought_id`, never the prepared audio. The current CoreS3 firmware
maps deliberate consent to `touch/tap/head_pat` and
`touch/stroke/head_stroke`; the state machine accepts both. It passes
`thought_id` plus OpenClaw-prepared, length-prefixed raw Opus packets to an
injected tell port and clears the offer only after playback succeeds. No text
summary or upstream `say` path exists.

The state machine suppresses duplicate playback while a thought ID remains in
bounded recent-ID memory for the running process. The playback endpoint also
uses `thought_id` for request idempotency. Restart or ID eviction may replay it.

The running process holds at most one offer. Restart persistence, quiet hours,
cooldowns, batching, and background-event selection remain outside Milestone 2
so this slice does not grow into a second memory or policy system.

The OpenClaw-facing boundary is
`gateway/openclaw_thought_service.py`. It exposes the same semantic decision
name but accepts a short message instead of encoded bytes. On the OpenClaw
host, it synthesizes the configured XC voice, normalizes it, encodes 16 kHz
mono PCM into 60 ms Opus packets, and submits the strict prepared-audio
contract over authenticated HTTPS. OpenClaw chooses the words; this producer
owns only deterministic audio preparation.

The body-facing boundaries are `gateway/pending_thought_service.py` for MCP
stdio and `gateway/pending_thought_http_service.py` for authenticated
Streamable HTTP. Both expose only `consider_thought` while keeping one
persistent authenticated upstream StackChan MCP session open. One
`PendingThoughtRuntime` owns the state machine and concrete body adapters for
that session lifetime. Custom `stackchan/event` notifications are dispatched
as tracked background work so playback cannot block the MCP receive loop, and
shutdown drains that work before closing the upstream session.

Both the stdio and HTTP boundaries restore the configured reviewed avatar and
require its exact checksum before accepting work. They then bind readiness to
the connected, initialized device session ID. A changed device session makes
readiness false and causes body actions to fail before movement or playback.
The HTTP boundary requires a distinct downstream bearer token when bound to a
non-loopback interface. A loopback-only bind may omit that credential.

Here, persistent means one session for the connected process lifetime, not an
internal reconnect loop. An upstream transport loss terminates the current
service. The runtime cannot be rebound because its machine and body are tied to
the original session, and Milestone 2 deliberately has no cross-session pending
state. Docker Compose starts a fresh runtime from the same TC image digest and
restores the reviewed avatar on each process start. A follow-up must verify the
exact pending-state loss after supervisor restart or disconnect.

### StackChan gateway

The gateway was imported from the `stackchan-mcp` `0.17.0` baseline and is now
maintained directly in XC Body. It provides the required shared-daemon
primitives:

- loopback Streamable HTTP MCP at `/mcp`;
- bearer authentication and allowed-host checks;
- authenticated ESP32 WebSocket connectivity;
- a bounded queue that serializes device-bound commands;
- health and status surfaces;
- existing hardware tools and servo safety limits.

XC Body extends this surface rather than creating another gateway or device
protocol. The semantic layer exposes only reviewed intention tools to
OpenClaw; raw movement tools remain behind that boundary.

### StackChan

- Applies configured face and mouth assets; semantic success additionally
  requires evidence that the selected face is physically visible.
- Can adopt a validated layered avatar set at runtime without a firmware flash;
  persistence across reboot has not been established.
- Drives head servos and LEDs.
- Reports device state.
- Microphone, camera, and touch-to-agent paths remain disabled or unused during
  Milestone 1.

The XC Body firmware provides a USB-only maintenance boundary. A
host tool can read connection state, persist the gateway URL and token, stream
logs, and request a normal application reboot. The token is accepted as input
but never returned. This path does not carry embodiment commands and does not
replace the authenticated WebSocket gateway.

## Shared-VM Isolation Boundary

Before deployment, perform a read-only host inventory and document existing
workload boundaries. XC Body requires its own service or container,
credentials, storage, lifecycle, health checks, resource limits, and
reverse-proxy routes. Its deployment, restart, rollback, and failure must not
disturb unrelated workloads.

## Security Boundary

- Use TLS for traffic crossing the Internet.
- Require a long bearer token or an equivalent authenticated tunnel.
- Never commit endpoint credentials, Wi-Fi credentials, or tokens.
- Do not expose the OpenClaw Gateway or device gateway without authentication.
- Do not enable camera or microphone transmission or expose a capture endpoint
  during Milestone 1.
- Treat generated avatar hashes and previews as build evidence, never as
  human-visible verification.
- Prefer allowlisting the minimal StackChan tool set visible to OpenClaw.
- Keep raw low-level movement tools behind the semantic embodiment boundary once
  the deterministic recipes exist.
- Do not invent a domain, assume that any port is open, or select a reverse
  proxy or TLS provider before the VM inventory.

## Recovery Boundary

Before flashing alternative firmware, record and preserve:

- Exact device model and hardware revision.
- Current firmware version.
- Official recovery image or verified download location.
- Flash command and serial port discovery procedure.
- Wi-Fi and official-app re-provisioning implications.
