# Roadmap

## Milestone Index

- [Milestone 1: OpenClaw Gets a Body](MILESTONE_1.md) — complete.
- [Milestone 2: Knock, Wait, Tell](MILESTONE_2.md) — complete.
- [Milestone 3: Continuity and Restraint](MILESTONE_3.md) — active.

The milestone files own current scope and acceptance. This roadmap describes
future direction only; it does not authorize implementation.

## Milestone 4: Ambient Life

Make StackChan feel quietly present between explicit interactions without
consuming model turns or constantly driving the servos.

Potential scope:

- sparse deterministic blinking and gaze shifts;
- subtle breathing or posture changes within reviewed servo limits;
- local touch reactions that do not create agent messages;
- time-aware waking, dimming, and sleep behavior;
- strict activity budgets that prevent repetitive motion; and
- immediate suspension of ambient behavior during active expressions,
  playback, reconnect recovery, or low-power states.

Ambient behavior remains local and deterministic. It must not invent thoughts,
speak without consent, expose private information, or compete with OpenClaw's
semantic intentions.

Milestone 4 succeeds when a multi-day physical run feels alive but not noisy,
wearing, or distracting, and all active interactions still take priority.

## Milestone 5: Direct Conversation and Senses

Add deliberate conversation and explicitly requested sensing while preserving
clear privacy boundaries.

Potential scope:

- a physical or explicit software action that starts voice input;
- visible indicators whenever microphone or camera capture is active;
- bounded speech recognition and response playback;
- user-requested single-frame vision rather than continuous observation;
- cancellation, timeout, and offline behavior that fail closed; and
- clear handling rules for captured audio, images, transcripts, and logs.

Always-on recording, passive face recognition, room surveillance, and hidden
capture are excluded. Camera or microphone use must be obvious to people near
the robot and must stop when the requested interaction ends.

Milestone 5 succeeds when a user can intentionally start and end a private,
understandable conversation without weakening the existing semantic, consent,
or recovery boundaries.

## Milestone 6: Stick S3 Continuity

Connect StackChan and XC Buddy as two physical channels for the same OpenClaw
identity without turning either device into a second agent.

Potential scope:

- StackChan remains the public home body;
- Stick S3 remains a portable, private channel;
- OpenClaw owns shared identity, memory, and judgment;
- offers and acknowledgments route to the appropriate available device;
- cross-device deduplication prevents repeated notifications;
- private content never moves to the public body without explicit consent; and
- either device remains usable when the other is offline.

This milestone requires an explicit cross-repository protocol and deployment
plan. XC Body must not modify `xc-buddy` without separate authorization.

Milestone 6 succeeds when switching devices feels like changing channels to
the same agent, with no duplicate personality, memory, or notification stream.

## Deferred Until Proven Valuable

- Always-on microphone or camera.
- Face recognition.
- Home Assistant integration.
- Free-form model-generated movement.
- Multiple robots.
- Rich simulated emotion models.
- Long autonomous monologues or generated dances.
- A new mobile companion application.
