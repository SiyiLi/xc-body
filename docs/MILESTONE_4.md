# Milestone 4: Direct Conversation and Appliance UX

## Objective

Make XC Body useful as a deliberate voice interface to the existing OpenClaw
agent while giving the device a simple, power-aware avatar, battery, and volume
experience.

Direct conversation and appliance UX establish a new firmware capability
contract. Candidate identifiers remain in source metadata and release
manifests until this milestone or a meaningful submilestone closes.

Milestone 4 has two pillars:

1. Louis can initiate a direct conversation by tapping and speaking to the
   robot.
2. The display behaves like a useful appliance with an avatar, battery status,
   settings, and restrained power behavior.

## Interaction Contracts

Two interaction origins are explicit and remain behaviorally separate.

### OpenClaw-initiated offer

An OpenClaw background completion may become a pending offer:

1. XC Body performs the accepted silent knock.
2. The avatar remains visible while the offer is pending.
3. XC Body waits for a deliberate head pat or stroke.
4. After acknowledgement, XC Body tells the prepared thought.

This remains the Milestone 2 and 3 `knock -> wait -> tell` contract. It is the
right behavior when OpenClaw initiates an interruption.

### Louis-initiated robot conversation

A direct question uses `ask -> attention move -> tell`:

1. Tapping the avatar's screen-right ear starts listening in manual-stop mode.
2. Tapping the screen-right ear again stops and submits the recording.
3. Tapping the screen-left ear while listening cancels and discards it.
4. The recognized question is transcribed and added as a real user turn in the
   existing Louis/XC OpenClaw conversation.
5. Telegram receives a clearly labelled mirror such as
   `🎙️ Louis via XC Body: <transcript>` so the complete conversation remains
   visible there.
6. OpenClaw runs one normal agent turn with the same context, tools, and
   policies as the bound Telegram session.
7. When the answer is ready, XC Body performs a short deterministic attention
   movement.
8. Speech begins only after the movement has physically settled.
9. The same final answer is delivered normally to Telegram and spoken once by
   XC Body.

A direct question never knocks, creates a pending offer, waits for head touch,
or requests a second confirmation. Its attention movement means "the answer is
ready; listen now," not "permission is required."

Robot-originated agent runs are explicitly marked and excluded from the
completion-offer classifier. An answer already spoken must not become a new
pending offer.

A direct conversation does not consume or replace an unrelated pending offer.
After the answer, the pending avatar is restored if that offer remains valid;
otherwise the display returns to the idle avatar.

## Conversation and Telegram Contract

The canonical conversation is the existing bound Louis/XC OpenClaw session,
not a separate robot assistant or isolated chat.

Telegram cannot attribute a bot-created transcript mirror to Louis's personal
account. The mirror must therefore identify its source honestly. This platform
limitation does not permit omitting the question: both the recognized question
and the final answer must remain visible when reviewing Telegram.

Each robot recording has one stable turn ID. At most one direct robot turn may
be active. The implementation must not duplicate an agent turn, Telegram
message, attention movement, or audio playback after an ambiguous network
failure.

## Voice Architecture

OpenClaw Gateway remains loopback-only on the Mac. Milestone 4 does not expose
it publicly and does not place a broad OpenClaw operator credential on the VM.

The minimal path is an outbound pull from the local native plugin:

```text
XC Body microphone
  -> existing Opus capture on the rendezvous VM
  <- authenticated long-poll by the local xc-body-native plugin
  -> native OpenClaw transcription
  -> normal turn in the existing Louis/XC session
  -> normal Telegram answer delivery
  -> authenticated final-text response to the VM
  -> accepted Edge TTS and prepared-Opus robot playback
```

The VM holds at most one completed capture in a bounded in-memory mailbox. It
is not a durable audio queue and must not replay accumulated turns after an
outage.

The local plugin:

- binds a configured robot endpoint, Telegram target, and OpenClaw session;
- does not accept arbitrary session or delivery targets from the VM;
- transcribes through OpenClaw's native media-understanding runtime;
- mirrors the recognized question to Telegram;
- admits one normal agent turn against the existing session;
- delivers the final answer through the ordinary Telegram path; and
- returns that exact visible answer to the VM for robot speech.

The VM reuses the physically accepted Edge TTS and prepared-Opus playback path.
Direct answers and pending-offer speech share the existing serialized
motion/audio boundary so they cannot overlap.

## Display and Settings Contract

The XC avatar is the main display. It remains full-screen in idle and while an
offer is pending.

Transient listening, thinking, attention, speaking, and error indications may
appear over the current base view. They do not create another persistent main
screen.

A swipe up opens one simple settings panel. Milestone 4 adds persisted output
volume as its first setting. Volume changes apply immediately and survive a
reboot.

The normal appliance view has one slim full-width translucent status bar. An
invisible grid places three equal icon slots on each side of a reserved center
area; grid cells and borders are never drawn. The continuous bar stays behind
the avatar, whose hat visually occludes its center. Wi-Fi uses
the outer-left slot, a pulsing red microphone uses the second-right slot while
listening, and the level-bar battery uses the outer-right slot. The battery
turns green while charging. There is no status text, clock, percentage, or
center content. A clear low-battery warning remains available.

The base-view priority is intentionally small:

1. settings while open;
2. transient interaction feedback while active;
3. avatar.

## Power Contract

When externally powered, XC Body keeps the avatar display available.

When battery powered, the display dims after 30 seconds and turns off after 60
seconds of safe inactivity. After five minutes it powers off; the left power
button boots it again. Reconnection restores authenticated control.

## Reused Foundations

Milestone 4 extends existing capabilities rather than replacing them:

- FT6336 screen tap and manual-stop listening;
- bounded Opus capture and authenticated audio-hook forwarding;
- OpenClaw native audio transcription and existing-session agent turns;
- accepted Edge TTS and prepared-Opus playback;
- serialized motion, settle, and playback behavior;
- CoreS3 output volume and NVS persistence;
- AXP2101 battery telemetry and charging indication; and
- connected display dimming.

## Implementation Plan

1. **Firmware UI:** keep the avatar full-screen, add the reviewed status grid,
   ear touch zones, swipe-up settings, and persisted volume control.
2. **Display arbitration:** restore the correct avatar after expiry,
   acknowledgement, or a direct conversation.
3. **Voice ingress:** enable the existing device-driven capture hook, add the
   one-turn VM mailbox, and poll it from the local native plugin.
4. **Conversation:** transcribe, mirror the labelled question to Telegram, and
   run exactly one normal turn in the existing OpenClaw session.
5. **Voice egress:** perform the reviewed short attention movement, wait for
   physical settle, then reuse accepted TTS and prepared-Opus playback.
6. **Power:** physically validate the candidate dim, display-off, and
   five-minute battery power-off policy as the acceptance gate.

Each slice must pass focused automated tests before deployment. Firmware,
runtime, or OpenClaw integration changes require exact-version physical
acceptance on the real robot.

## Milestone Exit Criteria

- Right ear, speak, and right ear produces one bounded recording and one
  transcription; left ear cancels without submission.
- The labelled recognized question and final answer are both visible in the
  existing Telegram conversation.
- The question is a real user turn in the existing OpenClaw session and may use
  its normal context and tools.
- XC Body performs one short attention movement after the answer is ready,
  waits until motion settles, and speaks the answer once without head-touch
  confirmation.
- A direct answer never creates a pending offer.
- An unrelated pending offer survives a direct conversation and regains its
  avatar afterward.
- OpenClaw offers retain `knock -> wait -> tell` behavior.
- Avatar, transient feedback, and settings transitions are deterministic.
- Battery level remains visible while charging and discharging.
- Volume changes immediately and survives reboot.
- External-power and battery-idle behavior pass physical acceptance without
  breaking authenticated control or wake behavior.
- Network ambiguity causes neither duplicate agent turns nor duplicate robot
  playback.

## Explicitly Deferred

- Always-on listening.
- Voice activity detection or automatic end-of-speech.
- Continuous or realtime conversation.
- Barge-in and streaming TTS.
- Durable audio or conversation queues.
- Multiple settings pages or a generic gesture framework.
- Free-form model-generated motion.
- Perfect charge-time estimation or sophisticated power profiles.
