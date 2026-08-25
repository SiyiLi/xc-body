# Architecture

## Topology

OpenClaw and StackChan run on separate hosts. Both connect outbound to an
isolated XC Body deployment on the cloud rendezvous host.

```text
OpenClaw host                         Cloud rendezvous
┌──────────────────────┐             ┌────────────────────────────┐
│ OpenClaw             │──HTTPS─────▶│ Caddy                      │
│ - completion plugin  │             │ - public TLS               │
│ - managed MCP client │             │ - authenticated routes     │
└──────────────────────┘             │                            │
                                     │ XC Body runtime image      │
StackChan K151/CoreS3                │ - gateway service          │
┌──────────────────────┐             │ - pending-thought service  │
│ XC Body firmware     │──WSS───────▶│ - summary and playback     │
└──────────────────────┘             └────────────────────────────┘
```

Caddy terminates public TLS at `https://43.143.37.91`. The gateway, avatar,
playback, summary, and XC Body MCP routes are proxied internally. Caddy also
serves versioned OTA app images and the current manifest from the read-only
`/data/xc-body/firmware` mount. Raw service ports remain private. The VM may
host unrelated workloads, so XC Body has its own containers, credentials,
lifecycle, health checks, and resource limits.

## Component Ownership

### OpenClaw

- Owns agent identity, reasoning, and the decision to express something.
- Selects semantic intentions instead of raw servo or animation commands.
- Observes successful agent, subagent, and cron completions.
- Classifies a completion as `offer` or `skip` through its native LLM runtime.
- Sends accepted bounded speech over authenticated HTTPS.

### Completion plugin

`openclaw-plugin/` observes typed completion hooks, including `agent_end`, and
deduplicates the same run across hook boundaries. It does not own speech
encoding, robot motion, pending-offer state, or device connectivity.

### Pending-thought service

The VM summary boundary keeps plaintext in request scope, prepares normalized
16 kHz mono Opus, validates the packet profile, and submits the existing
pending-thought contract. Plaintext is not stored or logged.

One process-owned runtime keeps at most one pending offer. It exposes only
`consider_thought`, receives StackChan events through one persistent upstream
MCP session, and owns the knock, wait, acknowledgment, and playback state.

### Semantic embodiment layer

The manual embodiment boundary validates the versioned intent contract and
maps supported intentions to immutable physical recipes. A complete recipe is
calibrated before the first device call. Expressive recipes make a mandatory
safe-return-to-idle attempt, including when the expression fails.

OpenClaw cannot choose servo angles, speed, hold duration, LED sequences, or
whether idle return occurs.

### StackChan gateway

`stackchan_mcp/` owns authenticated device WSS, loopback Streamable HTTP MCP,
allowed-host checks, command serialization, status, avatar transfer, playback,
and hardware tools. The semantic and pending-thought services use this shared
device boundary instead of defining another device protocol.

Raw movement tools remain behind the semantic boundary.

### StackChan firmware

The firmware drives the display, servos, LEDs, audio, touch events, and USB
maintenance channel. Deterministic firmware behaviors own expression timing,
head movement, local reaction ordering, and idle restoration.

The USB channel reports status, updates the saved gateway URL and token, queues
verified firmware metadata, streams logs, and requests an application reboot.
It has no network listener and never returns the saved token.

## Runtime Flows

### Manual embodiment

1. OpenClaw submits a versioned semantic intention.
2. XC Body validates the contract and complete local calibration.
3. The service restores and verifies the reviewed avatar for the device
   session.
4. The adapter invokes the deterministic face and motion recipe.
5. Every expressive recipe attempts the exact reviewed idle return.

### Completion offer

1. The OpenClaw plugin observes a successful eligible completion.
2. The shared fast-model projection classifies it as `offer` or `skip`.
3. An accepted short plain result crosses authenticated HTTPS unchanged;
   long or formatted results use the bounded Chinese projection.
4. The VM prepares and validates Opus before creating pending state.
5. Firmware performs one silent knock and returns to idle.
6. A deliberate head pat or head stroke acknowledges the current offer.
7. The VM sends the prepared audio for playback and clears the offer only
   after success.

The knock never receives prepared audio. No text-to-`say` fallback exists.

### Direct conversation

1. Existing firmware touch and device-driven capture submit one bounded Opus
   recording to the pending service mailbox.
2. The native OpenClaw plugin claims it, uses native media transcription, and
   admits one user turn into the configured existing session.
3. The final visible answer returns to the pending service exactly once.
4. The pending runtime requests a deterministic firmware-owned `attention`
   behavior through the shared StackChan gateway behavior boundary.
5. The gateway reuses its servo lane, correlated completion waiter, timeout,
   and recovery path. Prepared Opus playback starts only after the firmware
   reports physical settle and neutral return.
6. The pending offer, if any, is untouched and its base view is restored after
   either success or failure.
7. Each owner contributes content-free phase timings under the existing turn
   ID. The pending service emits one JSON timeline when a turn is answered or
   explicitly abandoned.

The direct flow does not call raw movement tools, create a pending offer, or
add another device transport.

Extract completed timelines from production logs with:

```sh
docker logs xc-body-pending 2>&1 |
  jq -R 'fromjson? | select(.event == "xc_body.direct_turn")'
```

### Firmware OTA

1. The publisher first builds the exact StackChan target. The packager accepts
   only an app whose embedded version matches the source and whose descriptor
   identifies XC Body StackChan.
2. The publisher uploads the versioned app, assets, and checksums, verifies the
   remote bytes, then replaces the stable manifest. Caddy serves the files
   read-only.
3. On boot, firmware checks the stable HTTPS manifest. If its version is newer,
   firmware verifies HTTPS, size, hash, image format, and StackChan identity
   while writing the inactive app partition.
4. On the new app's first boot, firmware proves the installed assets bytes and
   internal structure against the matching manifest. A mismatch triggers one
   bounded, in-place verified assets download and reloads the reviewed avatar.
   Power loss is non-atomic for this single assets partition; static fallback
   keeps the app bootable and a later boot retries.
5. The bootloader starts the new slot pending verification. Firmware marks it
   valid only after the authenticated gateway completes MCP tool discovery;
   otherwise it remains eligible for rollback.
6. After a rollback, the recovered slot records the failed version and disables
   automatic boot OTA until it is explicitly re-enabled through USB or the
   configuration screen.

The authenticated gateway maintenance tool can start the same verified update
without USB when an immediate update is needed. USB remains a recovery fallback.

## Avatar and Readiness Boundary

Semantic readiness is bound to the connected device session and the exact
reviewed avatar checksum. A valid but different payload is rejected. Command
success alone is not proof that a face is physically visible.

When the robot session changes, readiness becomes false. The service reloads
and checksum-verifies the reviewed avatar before accepting body work in the
new session. A still-valid in-process offer is retained during this recovery.

## State and Recovery

- One offer may wait at a time.
- An offer expires 30 minutes after its knock completes.
- Duplicate suppression is bounded to retained IDs in the running process.
- Robot reconnect recovery retains an unexpired offer in that process.
- Gateway transport loss replaces the upstream MCP session while preserving
  the in-process pending runtime.
- Process restart intentionally forgets offers and duplicate memory.
- OpenClaw submission retries are bounded and do not create a delayed queue.
- Connected idle display dimming does not drop the control transport.

There is no persistent queue, policy database, background replay, quiet-hours
engine, or cross-process pending state.

## Security and Safety Boundaries

- Internet traffic uses TLS. Control routes also require bearer authentication.
- Credentials, Wi-Fi settings, personal assets, and tokens stay out of Git.
- Raw gateway ports and `/capture` remain private.
- Camera and always-on microphone input are not enabled. Milestone 4 permits
  only deliberate, bounded tap-to-talk capture through the direct-conversation
  path above.
- OpenClaw chooses meaning; deterministic code chooses physical execution.
- Servo commands remain within reviewed yaw and pitch limits.
- Generated hashes and previews are build evidence, not physical acceptance.
- Deployment, OpenClaw reconfiguration, and firmware flashing require separate
  explicit permission.
