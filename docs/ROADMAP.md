# Roadmap

## Milestone Index

- [Milestone 1: OpenClaw Gets a Body](MILESTONE_1.md) — complete.
- [Milestone 2: Knock, Wait, Tell](MILESTONE_2.md) — complete.
- [Milestone 3: Continuity and Restraint](MILESTONE_3.md) — complete.
- [Milestone 4: Direct Conversation and Appliance UX](MILESTONE_4.md) —
  complete.
- [Milestone 5: Expression and Presence](MILESTONE_5.md) — active.
- Milestone 6: Explicit Camera Observation — future.

The milestone files own current scope and acceptance. This roadmap describes
future direction only; it does not authorize implementation.

## Milestone 4: Direct Conversation and Appliance UX

Completed on 2026-09-02 with real-user physical acceptance. Deliberate
tap-to-talk uses the existing Louis/XC OpenClaw conversation. A
best-effort labelled Telegram mirror records the recognized question, and the
final answer follows it after that delivery succeeds or fails. The robot
performs a short attention movement, waits for physical settle, and then speaks
the answer without the pending-offer consent cycle. Physical acceptance also
measures and bounds the wait from stopping the recording to hearing speech.

The device also added the then-current full-screen presence with compact status
icons, swipe-up volume settings, visible battery and charging state, and
restrained power-aware standby. Milestone 5 replaces that renderer while
preserving the accepted interaction behavior. See
[`MILESTONE_4.md`](MILESTONE_4.md) for the historical acceptance record.

## Milestone 5: Expression and Presence

The legacy layered renderer is replaced by saved expressions. Each expression
combines deterministic face animation and reviewed head movement. The `agree`
expression uses one restrained nod. `idle` is neutral presence, not an eighth
motor recipe.

Active scope:

- fixed `curious` replacement for direct attention and the offer knock;
- one speaking GIF owned by the existing TTS lifecycle;
- a USB-customizable `touch` recipe using the same expression schema;
- deterministic physical recipes and exact safe return;
- a USB-only loop to preview and store robot-specific motor calibration; and
- removal of the layered avatar, mouth, blink, fetch, and checksum lifecycle.

After the expression path is stable, idle presence may add sparse gaze or
posture changes and local touch reactions. Rest remains normal, activity is
strictly budgeted, and interaction or low-power state suspends ambient motion.
These local behaviors create no OpenClaw or Telegram traffic.

Direct conversation, background offers, and touch retain their existing state
machines; only their presentation implementation changes. See
[`MILESTONE_5.md`](MILESTONE_5.md) for the active boundary.

## Milestone 6: Explicit Camera Observation

Camera work begins only after the expression milestone. It allows the existing
OpenClaw identity to inspect an explicitly requested, bounded observation.

Potential scope:

- inspect the actual CoreS3 camera and deployed transport capabilities;
- capture one bounded observation after an explicit request;
- transfer it through the existing authenticated device boundary;
- make capture state and cancellation behavior visible; and
- return safely after success, failure, or cancellation.

## Deferred Until Proven Valuable

- Always-on microphone or camera.
- Face recognition.
- Home Assistant integration.
- Free-form model-generated movement.
- Multiple robots.
- Rich simulated mood models.
- Long autonomous monologues or generated dances.
- A new mobile companion application.
