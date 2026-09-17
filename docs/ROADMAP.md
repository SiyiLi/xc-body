# Roadmap

## Milestone Index

- [Milestone 1: OpenClaw Gets a Body](MILESTONE_1.md) — complete.
- [Milestone 2: Knock, Wait, Tell](MILESTONE_2.md) — complete.
- [Milestone 3: Continuity and Restraint](MILESTONE_3.md) — complete.
- [Milestone 4: Direct Conversation and Appliance UX](MILESTONE_4.md) —
  complete.
- [Milestone 5: Expression and Presence](MILESTONE_5.md) — complete.
- Milestone 6: Perception-Guided Ambient Presence — future.

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

Completed on 2026-09-17 with real-user physical acceptance of firmware and
assets `0.3.26`, Gateway and Interaction `0.3.8`, XC Body OpenClaw plugin
`0.3.2`, OpenClaw `2026.7.1-2`, and source commit `ffb56f0`.

The legacy layered renderer is replaced by saved expressions. Each expression
combines deterministic face animation and reviewed head movement. The `agree`
expression uses one restrained nod. `idle` is neutral presence, not an eighth
motor recipe.

Completed implementation scope:

- projection-selected expressions for direct responses and offer cues;
- one speaking GIF owned by the existing TTS lifecycle;
- a USB-customizable `touch` recipe using the same expression schema;
- deterministic physical recipes and exact safe return;
- a USB-only loop to preview and store robot-specific motor calibration; and
- removal of the layered avatar, mouth, blink, fetch, and checksum lifecycle.

Direct conversation and background offers retain their existing lifecycle.
Touch consent follows a successful local reaction and is ignored during direct
attention and speech.

## Milestone 6: Perception-Guided Ambient Presence

Milestone 6 is next but remains future work until its scope is approved. It
combines sparse local presence with bounded camera perception. For example, XC
Body may occasionally look for Elise while idle and direct its gaze toward her,
while the existing OpenClaw identity may also request one explicit bounded
observation.

Potential scope:

- add sparse camera-guided gaze or posture changes with strict activity
  budgets;
- suspend ambient motion during interaction and low-power states;
- keep ambient behavior local, with no OpenClaw or Telegram traffic;
- inspect the actual CoreS3 camera and deployed transport capabilities;
- evaluate recognition of Elise for local idle behavior;
- capture one bounded observation after an explicit request;
- transfer it through the existing authenticated device boundary;
- make capture state and cancellation behavior visible; and
- return safely after success, failure, or cancellation.

## Deferred Until Proven Valuable

- Always-on microphone or camera.
- Home Assistant integration.
- Free-form model-generated movement.
- Multiple robots.
- Rich simulated mood models.
- Long autonomous monologues or generated dances.
- A new mobile companion application.
