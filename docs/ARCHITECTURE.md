# Architecture

## Topology

OpenClaw and StackChan run on separate hosts. Both connect outbound to an
isolated XC Body deployment on the cloud rendezvous host.

```text
OpenClaw host                         Cloud rendezvous
┌──────────────────────┐             ┌────────────────────────────┐
│ OpenClaw             │──HTTPS─────▶│ Caddy                      │
│ - completion plugin  │             │ - public TLS               │
└──────────────────────┘             │ - authenticated routes     │
                                     │                            │
                                     │ XC Body runtime image      │
StackChan K151/CoreS3                │ - gateway service          │
┌──────────────────────┐             │ - Interaction service      │
│ XC Body firmware     │──WSS───────▶│ - summary and playback     │
└──────────────────────┘             └────────────────────────────┘
```

The OpenClaw plugin uses authenticated summary and voice HTTP routes. In the
deployed path, the Interaction service owns the private MCP connection to the
gateway.

Caddy terminates public TLS for robot WSS, Interaction HTTP, the isolated XC
Buddy status relay, and OTA files. Interaction reaches Gateway MCP and audio
directly over the private Docker network. Caddy also serves versioned OTA app
images and the current manifest from the read-only
`/data/xc-body/firmware` mount. Raw service ports remain private. The VM may
host unrelated workloads, so XC Body has its own containers, credentials,
lifecycle, health checks, and resource limits.

Gateway, Interaction, and relay stdout and stderr remain available through
Docker and are also persisted across container replacement in
`/data/xc-body/logs/`.

## Component Ownership

### OpenClaw

- Owns agent identity, reasoning, and the decision to express something.
- Selects semantic intentions instead of raw servo or animation commands.
- Observes successful agent, subagent, and cron completions.
- Classifies a completion as `offer` or `skip` through the fixed projection
  client.
- Sends accepted bounded speech over authenticated HTTPS.

### Native OpenClaw plugin

`openclaw-plugin/` observes typed completion hooks, including `agent_end`, and
deduplicates the same run across hook boundaries. Compound transcription and
spoken projection use the fixed model with reasoning and thinking disabled.
They choose from one fixed seven-expression semantic vocabulary. Idle is an
internal presence and safe-return state, not a model choice. The plugin does
not own speech encoding, robot motion, pending-offer state, or device
connectivity.

### Interaction service

The VM summary boundary keeps plaintext in request scope, prepares 16 kHz mono
Opus for pending offers, validates the packet profile, and submits
the existing pending-thought contract. Direct answers use the existing PCM
streaming path after the selected expression returns safely. Plaintext is not
stored or logged.

One process-owned runtime keeps at most one pending offer, receives StackChan
events through one persistent private MCP session, and serializes direct and
offer use of the body. It exposes authenticated HTTP only for the OpenClaw
plugin's summary and direct-conversation paths.

### XC Body gateway

`stackchan_mcp/` owns authenticated device WSS, private Streamable HTTP MCP,
allowed-host checks, command serialization, status, playback, and hardware
tools. The Interaction service uses this shared device boundary instead of
defining another device protocol.

When its private QWeather configuration is complete, the gateway reads
firmware's cached approximate public-IP coordinates, polls current conditions,
and forwards the provider's icon code, whole-degree temperature, and native
Chinese summary through the same device MCP session. Firmware resolves the
location on a low-priority worker after the first Wi-Fi connection. It reuses
a successful result across same-SSID reconnects and retries a failed lookup on
the next Wi-Fi connection event. It does not translate or invent weather text.
A VPN or shared network exit may produce the wrong city; an empty or failed
lookup falls back to central Shanghai.

Raw movement tools remain behind the semantic boundary.

### XC Buddy status relay

The auxiliary relay connects one fixed XC Buddy sender to one fixed XC Buddy
receiver over an authenticated WebSocket. It retains only the sender's current
lifecycle state. Completion and error notices are live-only and are never
replayed. Reconnecting receivers receive one silent current-state snapshot.

The relay runs as a sibling service with its own two role-specific credentials.
It receives no Gateway, Interaction, playback, OpenClaw, Telegram, or robot
credential and imports none of those execution paths. It has no persistent
queue, history, acknowledgements, or multi-client routing.

### StackChan firmware

The firmware drives the display, servos, LEDs, audio, touch events, and USB
maintenance channel. Deterministic firmware behaviors own expression timing,
head movement, local reaction ordering, and idle restoration.

Expression coordination does not execute servo ticks, GIF frames, or audio
samples. The head runner receives the complete trajectory and owns its fixed
clock, endpoint checks, abort, and measured safe return. The face player uses
the existing low-priority LVGL task and publishes only changed image regions.
The existing audio-output task remains the sole audio executor. Audio and head
deadlines take priority over visual deadlines; a late GIF frame may wait, but
it cannot run catch-up work inside either real-time executor. The neutral first
frame is prepared first; the head runner confirms the initial pose and torque
before the animation and trajectory begin.

The firmware also owns idle presence, optional listening animation, speaking
animation, the Milestone 4 idle screen timing, and LVGL rendering. Listening
art is display-only and missing art falls back to static idle without affecting
recording. Settings and non-appliance states suppress the face. The idle screen
can cover the face only while the robot is otherwise idle. Its first LCD or
head interaction is consumed to restore idle presence; the next interaction
follows the established action. Display dimming and sleep remain separate
`PowerSaveTimer` behavior.

The USB channel reports status, updates the saved gateway URL and token, queues
verified firmware metadata, streams logs, and requests an application reboot.
It also parses expression calibration requests and immediately reports whether
the board-owned runner admitted a transient preview. The runner then executes
independently. The USB channel has no network listener and never returns the
saved token. Production tools select only a saved expression name and cannot
supply motor parameters.

## Runtime Flows

### Completion offer

1. The OpenClaw plugin observes a successful eligible completion.
2. The shared fast-model projection classifies it as `offer` or `skip` and
   selects one non-idle expression for an offer.
3. An accepted short plain result crosses authenticated HTTPS unchanged;
   long or formatted results use the bounded Chinese projection. The selected
   expression crosses the same request.
4. The VM prepares and validates Opus, then asks firmware to suppress the idle
   screensaver while the offer transition runs.
5. Firmware performs the selected expression and returns safely to idle.
6. Only after the expression completes does the VM create pending state. A
   failed expression clears the display hint and drops the offer.
7. When direct attention and speech are inactive, a deliberate head pat or
   stroke starts the local touch reaction. Its successful safe return emits a
   touch event. The VM acknowledges its current offer or discards the event
   when no offer exists.
8. The VM sends the prepared audio for playback and clears the offer only
   after success.

The expression never receives prepared audio. No text-to-`say` fallback exists.

A root robot-originated completion is ineligible because its answer follows
the direct path. A descendant subagent completion remains eligible, allowing a
long-running direct request to offer optional progress without delaying its
eventual direct answer.

### Direct conversation

1. Existing firmware touch and device-driven capture submit one bounded Opus
   recording to the Interaction service mailbox.
2. The native OpenClaw plugin claims it and sends the captured Ogg to compound
   transcription. The result contains the transcript and route, plus a named
   expression when a silent expression is a natural and complete response.
3. That expression-only route skips the agent. Questions, requests requiring
   action or explanation, and uncertain cases enter the configured existing
   OpenClaw session.
4. Every completed answer is projected to select one of the seven named
   expressions from its full meaning. A short answer keeps its exact speech; a
   long or formatted answer is also projected into bounded speech.
5. The plugin sends expression and optional speech once through the claimed
   voice turn. Interaction holds its body lane while Gateway runs the firmware
   expression through safe return and then starts PCM when speech is present.
6. The pending offer, if any, is untouched. Head touch is ignored during
   direct attention and speech; after they end, a new successful touch
   reaction may acknowledge the offer.
7. Each owner contributes content-free phase timings under the existing turn
   ID. Interaction emits one JSON timeline when a turn is answered or
   explicitly abandoned.

Direct conversation is permanently bound to one fixed Telegram private chat.
Telegram groups, supergroups, channels, forum topics, and negative chat IDs are
not supported and are not future scope for XC Body.

The root direct flow does not call raw movement tools, create a pending offer,
or add another device transport. Its descendant subagent completions may enter
the normal background-offer path independently.

Extract completed timelines from production logs with:

```sh
rg '"event":"xc_body.direct_turn"' server-logs/interaction.log |
  tail -n 1 | jq .
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
   bounded, in-place verified assets download. Power loss is non-atomic for
   this single assets partition; the app remains bootable only for repair and
   a later boot retries. A missing, corrupt, or undecodable named expression
   GIF fails that behavior before motor movement.
5. The bootloader starts the new slot pending verification. Firmware marks it
   valid only after the authenticated gateway completes MCP tool discovery;
   otherwise it remains eligible for rollback.
6. After a rollback, the recovered slot records the failed version and disables
   automatic boot OTA until it is explicitly re-enabled through USB or the
   configuration screen.

The authenticated gateway maintenance tool can start the same verified update
without USB when an immediate update is needed. USB remains a recovery fallback.

### Idle screen

1. On the first Wi-Fi connection after boot, firmware resolves its approximate
   coordinates through a keyless HTTPS public-IP lookup on a low-priority
   worker. It caches successful results, retries a failure on the next Wi-Fi
   connection event, and serves cache reads immediately. The gateway waits one
   device-check interval for each new session, then refreshes QWeather and
   continues hourly before pushing changed data.
2. After 60 seconds without interaction, firmware may cover idle presence and
   the appliance status row with the local clock, date, weather icon, provider
   summary, and temperature.
3. The idle-view fonts and RGB565A8 weather icons are mapped from the assets
   partition rather than linked into either application slot.
4. The Interaction runtime asks firmware to suppress the overlay during an
   offer transition and pending wait, and restores that display hint after a
   device reconnect.
5. Settings, transient behavior, listening, and speaking suppress the idle
   screen. The first LCD or head touch restores idle presence and is consumed;
   a later interaction performs its normal action. The independent power
   policy may still dim or sleep the display.

## Expression and Readiness Boundary

Semantic readiness is bound to the connected and initialized device session.
When the session changes, readiness becomes false until the service observes
the replacement session. A still-valid in-process offer is retained during
this recovery.

Expression assets ship in the firmware assets partition. Motor recipes are
selected by name and stored in NVS through USB. The gateway never transfers
face layers, checksums a runtime face package, or accepts raw recipe data.
The deterministic asset generator gives every packaged GIF the same canonical
opaque background and preserves efficient delta frames. A transition redraws
the complete face once; subsequent frames redraw only their changed regions.
The named GIF and motor recipe are one expression: a named-asset load or decode
failure is a critical release fault, not a blank-face fallback.

## State and Recovery

- One offer may wait at a time.
- An offer expires 30 minutes after its physical cue completes.
- Duplicate suppression is bounded to retained IDs in the running process.
- Robot reconnect recovery retains an unexpired offer in that process.
- Pending-offer display state is resynchronized after robot reconnect.
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
