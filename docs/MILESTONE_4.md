# Milestone 4: Direct Conversation and Appliance UX

## Objective

Make XC Body useful and responsive as a deliberate voice interface to the
existing OpenClaw agent while giving the device a simple, power-aware avatar,
battery, and volume experience.

Direct conversation and appliance UX establish a new firmware capability
contract. Milestone 4 closed on 2026-09-02.

Milestone 4 has three pillars:

1. Louis can initiate a direct conversation by tapping and speaking to the
   robot.
2. The display behaves like a useful appliance with an avatar, battery status,
   settings, and restrained power behavior.
3. The wait from stopping a recording to hearing the answer is measured and
   short enough to preserve conversational flow.

## Current Status

Milestone 4 has physical acceptance. The complete direct-conversation and
appliance experience felt good through real user tests during regular use.
That judgment, rather than a synthetic engineering exercise, is the golden
acceptance criterion. Metrics and scripted checks support diagnosis and
reproducibility but do not replace it.

The accepted candidate was:

- XC Body source `c2685f6bf8064e0826c92edddc55a352f7622567`;
- XC Body gateway `0.2.9`, image SHA-256
  `68ac7ba35f23b7dc4f3547615995d8fa93bfc4304e0a4ca754f6e2a6056ed123`;
- XC Body firmware `0.2.18`;
- XC Body OpenClaw plugin `0.2.3`; and
- OpenClaw `2026.7.1-2` (`0790d9f`).

In one real-user, physically accepted healthy-Wi-Fi conversation, all `406`
sent frames were accepted and output, no frame was rejected, and `24.36`
seconds of speech had a maximum measured codec-write gap of `78 ms`. No audio
loss, pause, or pop/click was heard. Two longer clean observations of
`49.44` and `51.84` seconds also showed no cumulative cadence drift. A
degraded-condition `1,450 ms` diagnostic gap remains useful evidence for future
tolerance work, but it did not block acceptance of the healthy product
experience.

### Weather icon acceptance record

On 2026-08-27, the human observer accepted all 20 weather families on the
CoreS3 display. The synchronized sweep held each icon for ten seconds over the
real idle-screen background. All icons had clean transparent edges, all 20
device calls succeeded, and the robot remained stable for the five-minute
run. The production gateway was restored healthy afterward.

Exact test inputs were:

- XC Body firmware `0.2.13` app SHA-256:
  `a6a39d38aea15412bdea60af1eca0ef7d5501565f8893533d5aec706774512dc`
- XC Body assets `0.2.13` SHA-256:
  `0ad8419b3b3c0ada26ff8ba7f890de3fa5604740682d5e930df410db8cff8fa4`
- XC Body gateway `0.2.2` image digest:
  `ee836572ccfff837c3ceef0e5a86fab26e28b5f6c9dc741a0a82a979877307ac`
- OpenClaw version: none. The test called the device MCP directly and did not
  exercise OpenClaw.
- XC Body source: the repository commit containing this acceptance record.

The physical artifacts were built from the same firmware source and weather
assets recorded in that commit.

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
5. XC Body sends Telegram a clearly labelled best-effort mirror such as
   `🎙️ Louis via XC Body: <transcript>` so the complete conversation remains
   visible there when delivery succeeds. A mirror failure does not block the
   agent turn, but the final answer is sent only after the mirror succeeds or
   fails.
6. OpenClaw runs one normal agent turn with the same context and policies as
   the bound Telegram session. The local plugin owns final Telegram delivery.
7. When the answer is ready, XC Body performs a short deterministic attention
   movement.
8. Speech begins only after the movement has physically settled.
9. The full final answer is delivered normally to Telegram. Short plain
   English or Chinese answers are spoken directly. Long or formatted
   answers use the shared fast-model projection and are spoken once in Chinese
   with limits of 200 words and 1000 Unicode characters.
10. Projection receives the complete answer and retries once. If both attempts
    fail, the direct path speaks a short error message rather than the answer.

A direct question never knocks, creates a pending offer, waits for head touch,
or requests a second confirmation. Its attention movement means "the answer is
ready; listen now," not "permission is required."

The root robot-originated agent completion is excluded from the
completion-offer classifier so its direct answer cannot become a duplicate
pending offer. A descendant subagent completion remains eligible for the
normal background-offer classifier. For a long-running request, this may offer
meaningful progress while the root agent continues, and Louis chooses whether
to hear it by touching the robot's head.

A direct conversation does not consume or replace an unrelated pending offer.
After the answer, the pending avatar is restored if that offer remains valid;
otherwise the display returns to the idle avatar.

## Conversation and Telegram Contract

The canonical conversation is the existing bound Louis/XC OpenClaw session,
not a separate robot assistant or isolated chat.

Telegram cannot attribute a bot-created transcript mirror to Louis's personal
account. The mirror must therefore identify its source honestly. It is
best-effort observability and never gates the agent turn. The final answer is
sent after the mirror succeeds or fails.

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
  -> fixed-model audio transcription
  -> normal turn in the existing Louis/XC session
  -> normal Telegram answer delivery
  -> shared fast-model projection when the answer is not TTS-friendly
  -> authenticated spoken-text response to the VM
  -> Edge TTS and bounded PCM robot playback
```

The VM holds at most one completed capture in a bounded in-memory mailbox. It
is not a durable audio queue and must not replay accumulated turns after an
outage.

The local plugin:

- binds a configured robot endpoint, Telegram target, and OpenClaw session;
- does not accept arbitrary session or delivery targets from the VM;
- transcribes the captured Ogg through fixed-model audio inference;
- mirrors the recognized question to Telegram;
- admits one normal agent turn against the existing session;
- delivers the full final answer through the ordinary Telegram path; and
- returns either a short plain answer or its concise Chinese projection to the
  VM for robot speech.

The VM reuses accepted Edge TTS and the existing serialized motion/audio
boundary. Direct answers fill a bounded PCM buffer during attention, then use
the existing PCM stream after attention settles and a safe initial buffer is
ready. Pending offers retain their complete prepared-Opus path.

## End-to-End Latency Contract

The primary user-perceived response latency starts when the second right-ear
tap stops and submits the recording and ends when robot speech starts. The
existing content-free timeline reports this as
`submit_to_speech_start_ms`.

Recording duration is controlled by Louis and is therefore reported
separately. `end_to_end_ms`, from recording start through turn completion,
remains useful for diagnosing the whole interaction but must not be presented
as response latency.

Fixed short-question cohorts and numeric summaries remain useful diagnostics,
especially when comparing candidates. They are not a substitute for the
real-user acceptance record. Optimization follows the slowest measured phase
in the existing timeline; it must not add another tracing path or bypass the
normal OpenClaw session.

A repeatable reference cohort uses five consecutive fixed short questions that
require no slow external tool. Record every `submit_to_speech_start_ms` sample,
the median and worst result, and the exact source, firmware, gateway, OpenClaw
plugin, and OpenClaw versions. Record tool-using questions separately because
their agent work is intentionally variable. This is a comparison method, not
an acceptance gate.

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

After 60 seconds of safe idle with no pending offer, a full-screen local view
replaces the avatar and status bar with six centered elements: hour, minute,
weather icon, QWeather's native Chinese summary, temperature, and date. Any
LCD or head touch restores the avatar before the existing interaction
continues. Direct conversation, transient behavior, settings, and pending
offers suppress the idle view.

The base-view priority is intentionally small:

1. settings while open;
2. transient interaction feedback while active;
3. pending-offer avatar;
4. idle clock and weather after its timer expires; and
5. ordinary avatar.

## Power Contract

When externally powered, XC Body keeps the avatar display available.

When battery powered, the display dims after 30 seconds and turns off after 60
seconds of safe inactivity. After five minutes it powers off; the left power
button boots it again. Reconnection restores authenticated control.

## Reused Foundations

Milestone 4 extends existing capabilities rather than replacing them:

- FT6336 screen tap and manual-stop listening;
- bounded Opus capture and authenticated audio-hook forwarding;
- fixed-model audio transcription and existing-session agent turns;
- accepted Edge TTS and prepared-Opus playback;
- serialized motion, settle, and playback behavior;
- CoreS3 output volume and NVS persistence;
- AXP2101 battery telemetry and charging indication;
- QWeather current conditions through the existing device MCP session; and
- connected display dimming.

## Implementation Plan

1. **Firmware UI:** keep the avatar full-screen, add the reviewed status grid,
   idle clock and weather, ear touch zones, swipe-up settings, and persisted
   volume control.
2. **Display arbitration:** restore the correct avatar after expiry,
   acknowledgement, or a direct conversation.
3. **Voice ingress:** enable the existing device-driven capture hook, add the
   one-turn VM mailbox, and poll it from the local native plugin.
4. **Conversation:** transcribe, mirror the labelled question to Telegram, and
   run exactly one normal turn in the existing OpenClaw session.
5. **Voice egress:** use one shared prompt and fixed fast-model projection to
   classify offers and project long or formatted results into Chinese speech of
   at most 200 words and 1000 Unicode characters. Short plain English or
   Chinese is spoken unchanged. Retry once, then apply the caller-specific
   failure policy before direct PCM playback. Pending offers retain prepared
   Opus playback.
6. **Latency:** use physical response timelines to identify the slowest owned
   phase and improve it without weakening session continuity or delivery
   correctness.
7. **Power:** observe the candidate dim, display-off, and five-minute battery
   power-off policy during real use.

Each slice must pass focused automated tests before deployment. Firmware,
runtime, or OpenClaw integration changes require exact-version physical
acceptance on the real robot.

## Reference Validation Scenarios

These scenarios remain useful for engineering diagnosis and regression work.
They are references and do not determine milestone closure.

- Right ear, speak, and right ear produces one bounded recording and one
  transcription; left ear cancels without submission.
- When transcript mirror delivery succeeds, the labelled recognized question
  and final answer are both visible in the existing Telegram conversation.
- The question is a real user turn in the existing OpenClaw session and may use
  its normal context and tools.
- XC Body performs one short attention movement after the answer is ready,
  waits until motion settles, and speaks one voice-friendly rendering without
  head-touch confirmation.
- A root direct answer never creates a pending offer; meaningful descendant
  subagent completions may offer optional progress.
- An unrelated pending offer survives a direct conversation and regains its
  avatar afterward.
- OpenClaw offers retain `knock -> wait -> tell` behavior.
- Avatar, transient feedback, and settings transitions are deterministic.
- The idle clock and weather never cover settings, an interaction, or a
  pending offer, and touch restores the avatar without losing that touch.
- Battery level remains visible while charging and discharging.
- Volume changes immediately and survives reboot.
- External-power and battery-idle behavior preserve authenticated control and
  wake behavior.
- Network ambiguity causes neither duplicate agent turns nor duplicate robot
  playback.
- A completed direct turn emits one content-free JSON timing record covering
  capture, OpenClaw processing, speech preparation, attention, and playback.
- `gateway_first_audio_frame_sent_ms` records the first frame sent toward the
  device, not physical sound onset; `submit_to_speech_start_ms` uses it as the
  available proxy.
- Content-free direct-turn timelines remain available for latency comparison
  and diagnosis.

## Explicitly Deferred

- Always-on listening.
- Voice activity detection or automatic end-of-speech.
- Continuous or realtime conversation.
- Barge-in and realtime streaming speech interaction.
- Durable audio or conversation queues.
- Multiple settings pages or a generic gesture framework.
- Free-form model-generated motion.
- Perfect charge-time estimation or sophisticated power profiles.
