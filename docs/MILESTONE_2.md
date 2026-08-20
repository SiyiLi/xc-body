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
- Executable downstream MCP stdio and Streamable HTTP services exposing only
  `consider_thought`.
- A local OpenClaw producer that converts its short spoken message into the
  reviewed prepared-audio profile before calling the remote service.
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

The producer profile is raw Opus encoded from 16 kHz mono PCM in 60 ms packets.
Each packet is framed by a
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
The tell port receives `thought_id` and prepared audio and forwards the ID as
the playback endpoint's idempotency key. Completed outcomes are retained in a
fixed-size in-process bound; the currently pending offer is never evicted.
Suppression lasts only while an ID remains retained in the running process.
Restart or ID eviction may replay it.

## Upstream Event Boundary

The accepted acknowledgment event matches the gateway notification shape:

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

The stdio and Streamable HTTP entry points expose only `consider_thought`. Each
opens one persistent upstream StackChan MCP session, owns one
`PendingThoughtRuntime` for that session lifetime, and continuously receives
the custom `stackchan/event` notification.

An accepted offer calls one named firmware behavior. The robot owns the
`thinking` face, head pose `(12,50)` at low speed, ten-second hold, neutral
return `(0,43)`, idle restoration, and completion event. The gateway waits for
that event. A head gesture is emitted only after its local reaction settles;
the cloud then plays the prepared Opus packets. Retained recent IDs suppress
duplicate playback within the running process; restart or ID eviction may
replay it. There is no text-to-`say` fallback.

Before either transport accepts work, the service restores the configured
avatar archive, requires the exact reviewed checksum, and records the connected
and initialized device session. Readiness and body actions recheck that session;
a device reconnect therefore fails closed instead of using placeholder assets.
The HTTP surface requires its separate downstream bearer token on a
non-loopback bind. A loopback-only bind may omit it.

The process keeps one upstream session while connected. It exits on transport
loss rather than rebinding the session-owned runtime. The tracked rendezvous
deployment publishes the exact XC Body source and reviewed avatar as one TC
Artifactory runtime image. The VM runs
that digest as the gateway and persistent pending-thought service. Internal
reconnect and verified pending-state loss after supervisor restart remain
follow-up work.

OpenClaw does not call the binary contract directly. Its tracked local producer
accepts the agent's decision and short message, uses the configured XC voice,
normalizes the result, and emits 16 kHz mono, 60 ms Opus packets. The
gateway sends the accepted six-packet prefill, then paces remaining packets at
the device's 60 ms consumption interval. This preserves continuous playback.

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

The persistent pending-thought service is an intentional prerequisite for this
acceptance path: it restores the reviewed avatar before readiness and keeps the
event subscription alive while an offer waits. The measured ten-second hold
keeps the silent knock visible while preserving deterministic return to idle.

## Exit Criteria

Milestone 2 is complete. OpenClaw selected `offer` and authored the spoken
Chinese message. A physical head stroke triggered complete playback, and Louis
confirmed that the speech was clear.

The accepted versions were source candidate
`e5e1fd68824fd80322896ddbaa7f23a06b34f2a7`, StackChan source
`804af573ba8f577f63efbd39f6e8a9c7f57b4647`, firmware `2.2.6` image SHA-256
`c12ffb705d71c3ece5d78f3f2369c590b230a4c388432b5616c3ebfe671f175c`,
runtime image SHA-256
`5f1764fa4bba8eba7ee60891a18b13994fd8d1ca7cc50abb887001202c5d19cc`,
Caddy image SHA-256
`98eb57d882ccd5213d1688764db10c1ca2c58a1ca3a6717a3411ad798f7a423a`,
and OpenClaw `2026.7.1-2 (0790d9f)`. Restart persistence and cross-session
recovery remain Milestone 3 work.
