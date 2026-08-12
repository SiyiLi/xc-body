# Architecture

## Current Deployment Fact

OpenClaw and StackChan run on separate hosts and connect outbound to an
always-on cloud rendezvous. The rendezvous host may also run unrelated
workloads, which must remain isolated from XC Body.

A read-only inventory confirmed that the rendezvous host has Docker and enough
headroom for a separately isolated XC Body service. Existing workloads and
ports must remain untouched. No existing public reverse-proxy/TLS route suitable
for XC Body was found, so endpoint naming, certificate termination, and firewall
changes remain explicit future deployment decisions rather than assumptions.
Nothing on the host was changed during discovery.

The transport-independent semantic core is dependency-free Python 3.10+, which
matches the pinned upstream gateway's language floor. Deployment packaging is
not selected yet.

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
outbound, so the home router needs no inbound device route. A future deployment
should terminate both paths on a reviewed public TLS route, preferably TCP 443.
Raw gateway ports 8765, 8766, and 8767 must not be publicly exposed.

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

The implemented slice includes one transport-neutral semantic `embody` tool,
an injected synchronous device port, a fail-closed adapter, and an upstream MCP
client wrapper. The cloud runner confines MCP SDK async behavior to its session
boundary and executes the synchronous semantic path in a worker thread. URL
and bearer token values have no code defaults. Importing the runner starts no
session and performs no environment reads.

Calibration remains explicit and immutable. Upstream avatar-name mapping and
human-visible face verification are separate facts. A complete recipe,
including mandatory idle return, resolves both facts before device work. An
unverified face raises a typed visible-face verification error with zero client
calls. The runner applies the measured calibration preflight before endpoint
configuration or MCP session creation.

The reviewed deployment factory preserves measured idle and curious motion and
maps four upstream avatar names, but its visibly verified face set is empty.
Pleased and concerned motion also remains incomplete. Servo commands are
checked against upstream yaw `-90..90` and pitch `5..85` limits at calibration
construction. Real hardware confirmed curious head movement and exact neutral
recovery, but not visible expression rendering.

### stackchan-mcp

The pinned `0.17.0` revision was inspected at its exact git revision without
initializing the submodule. Its gateway requires Python 3.10+ and already
provides the required shared-daemon primitives:

- loopback Streamable HTTP MCP at `/mcp`;
- bearer authentication and allowed-host checks;
- authenticated ESP32 WebSocket connectivity;
- a bounded queue that serializes device-bound commands;
- health and status surfaces;
- existing hardware tools and servo safety limits.

XC Body should adapt this surface rather than duplicate its gateway or device
protocol. The semantic layer must expose only reviewed intention tools to
OpenClaw; raw movement tools remain behind that boundary.

### StackChan

- Applies configured face and mouth assets; semantic success additionally
  requires evidence that the selected face is physically visible.
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
