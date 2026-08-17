# Milestone 2: Knock, Wait, Tell

## Objective

Let the existing OpenClaw agent offer one meaningful background result without
playing prepared audio until the user acknowledges it with a deliberate head
gesture.

The experience is:

1. OpenClaw classifies a background result as `ignore`, `remember`, or `offer`.
2. `ignore` and `remember` cause no physical action.
3. `offer` produces one silent, deterministic knock and waits.
4. A deliberate StackChan head pat or head stroke acknowledges the pending
   offer.
5. XC Body plays OpenClaw-prepared Opus packets after acknowledgment.

## Scope

In scope:

- A strict versioned pending-thought contract.
- Exactly three decisions: `ignore`, `remember`, and `offer`.
- One pending offer at a time.
- A silent knock that does not disclose prepared audio.
- Upstream `stackchan/event` head-pat or head-stroke acknowledgment.
- Bounded in-process duplicate suppression keyed by `thought_id`.
- Fake-port tests for validation, transitions, duplicates, and failures.
- One executable downstream MCP stdio service exposing only
  `consider_thought`.
- One persistent authenticated upstream StackChan MCP session that receives
  device events and owns the pending-thought runtime for its lifetime.

Out of scope:

- Choosing which real OpenClaw background events become thoughts.
- Persistence across process or machine restarts; that is Milestone 3.
- Quiet hours, cooldowns, batching, or priority policy.
- Microphone input, camera input, or always-on sensing.
- Free-form model-generated motion.
- Speaking private or unreviewed text into the room.

## Pending-Thought Contract

The classification boundary accepts:

```json
{
  "version": "v1",
  "thought_id": "eval:run-42",
  "decision": "offer",
  "audio_base64": "ABRYAvkwTbsN5eOSCYk468rhsdHdhQ=="
}
```

`thought_id` keys bounded in-process duplicate suppression. `audio_base64` is
required only for an `offer`; it is forbidden for `ignore` and `remember`. Its
raw JSON string is limited to 1,048,576 characters before surrounding
whitespace is stripped.

The producer profile is raw Opus encoded from 16 kHz mono PCM in 60 ms packets,
matching the pinned upstream producer defaults. Each packet is framed by a
two-byte big-endian, nonzero packet length. XC Body validates complete framing,
bounded packet count and size, and the Opus TOC's mono flag and 60 ms packet
duration. It does not decode packets, so decodability and the 16 kHz PCM input
rate remain producer guarantees rather than claims inferred from packet bytes.
No text is passed to an upstream `say` tool.

See `contracts/pending-thought.schema.json` for the tracked schema.

## State Machine

| Decision or event | Previous state | New state | Physical effect |
| --- | --- | --- | --- |
| `ignore` | unseen | ignored | none |
| `remember` | unseen | remembered | none |
| `offer` | no pending offer | waiting | one silent knock |
| deliberate head pat or stroke | waiting | told | play prepared audio |
| unrelated event | any | unchanged | none |
| retained duplicate thought | retained recent outcome | unchanged | none |

A second offer cannot replace the thought already awaiting acknowledgment. A
tell failure leaves the offer pending so the failure is honest and retryable.
The tell port receives `thought_id` and prepared audio, and must use the ID as
its duplicate-suppression key. Completed outcomes are retained in a fixed-size
in-process bound; the currently pending offer is never evicted. Suppression
lasts only while an ID remains retained in the running process. Restart or ID
eviction may replay it.

## Upstream Event Boundary

The accepted acknowledgment event matches the pinned gateway's notification
shape exactly:

```json
{
  "event_type": "touch",
  "subtype": "tap",
  "action": "head_pat"
}
```

Current CoreS3 firmware maps deliberate consent to either
`touch/tap/head_pat` or `touch/stroke/head_stroke`; both are accepted. Other
touch events, or either gesture while no offer is pending, are no-ops.
Milestone 2 does not reinterpret arbitrary device events as consent.

## Executable Service

`gateway/pending_thought_service.py` exposes only `consider_thought` over MCP
stdio. It opens one persistent upstream StackChan MCP session, owns one
`PendingThoughtRuntime` for that session lifetime, and continuously receives
the custom `stackchan/event` notification.

An accepted offer produces the reviewed silent knock: the `thinking` face,
head pose `(12,50)` at low speed, a bounded hold, and return to neutral
`(0,43)` plus `idle`. Either accepted head gesture plays the prepared Opus
packets. Retained recent IDs suppress duplicate playback within the running
process; restart or ID eviction may replay it. There is no text-to-`say`
fallback. Gesture work runs outside the MCP receive loop, and service shutdown
waits for in-flight event work before closing the upstream session.

The process keeps one upstream session while connected. It exits on transport
loss rather than rebinding the session-owned runtime. Internal reconnect,
dependency pins, and a reviewed launch/supervisor definition remain follow-up
work; current documentation does not treat source files alone as a
reproducible deployment.

## Acceptance Tests

1. `ignore` and `remember` produce no body call.
2. Each accepted `offer` makes one silent knock without exposing prepared
   audio to the knock port.
3. Only the current CoreS3 head-pat and head-stroke events acknowledge it.
4. A successful gesture plays validated prepared audio and clears it.
5. Retained duplicate submissions and repeated taps do not repeat the knock or
   prepared-audio playback within the running process.
6. A second offer cannot replace the pending thought.
7. A tell failure remains visible as an error and keeps the offer pending.
8. Invalid or overlong payloads fail before a body call.
9. A batched real-device run proves knock, gesture, and playback integration.

The persistent semantic service is an intentional prerequisite for this
acceptance path: it serializes each complete recipe through idle and restores
the reviewed avatar before readiness. The measured ten-second curious hold is
also intentional acceptance hardening: it keeps the silent knock visible long
enough for deliberate acknowledgment while preserving deterministic return to
idle.

## Exit Criteria

The dependency-free state machine, contract, executable one-tool MCP service,
persistent upstream session, and StackChan event routing are implemented and
pass local fake-port and real-SDK transport tests. On 2026-08-14, Louis
confirmed the full physical experience: silent offer, deliberate head gesture,
and playback of OpenClaw-prepared audio through StackChan. Milestone 2 is
complete. Restart persistence and cross-session recovery remain Milestone 3
work.
