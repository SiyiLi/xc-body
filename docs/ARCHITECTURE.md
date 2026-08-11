# Architecture

## Current Deployment Fact

OpenClaw and StackChan run on separate hosts and connect outbound to an
always-on cloud rendezvous. The rendezvous host may also run unrelated
workloads, which must remain isolated from XC Body.

The VM's reverse proxy, TLS provider, firewall, open ports, and package or
container state have not yet been inventoried for XC Body. Runtime language and
packaging remain undecided until that read-only inventory and the pinned
upstream reuse boundary are inspected.

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

OpenClaw connects outbound and authenticated to the XC Body MCP HTTP surface.
StackChan also initiates its authenticated WSS connection outbound, so the home
router needs no inbound device route. WSS should preferably use public TCP 443
through the VM's existing or selected reverse proxy. Raw gateway ports 8765,
8766, and 8767 must not be publicly exposed.

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

### stackchan-mcp

- Owns the device WebSocket connection.
- Provides the shared Streamable HTTP daemon/gateway used by the isolated XC
  Body service.
- Exposes existing hardware capabilities through MCP without exposing raw
  device ports publicly.
- Enforces established device and servo safety limits.
- Provides the initial firmware and gateway implementation.

### StackChan

- Renders faces and mouth state.
- Drives head servos and LEDs.
- Reports device state.
- Microphone, camera, and touch-to-agent paths remain disabled or unused during
  Milestone 1.

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
