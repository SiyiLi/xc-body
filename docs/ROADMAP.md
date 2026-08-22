# Roadmap

## Milestone Index

- [Milestone 1: OpenClaw Gets a Body](MILESTONE_1.md) — complete.
- [Milestone 2: Knock, Wait, Tell](MILESTONE_2.md) — complete.
- [Milestone 3: Continuity and Restraint](MILESTONE_3.md) — complete.
- [Milestone 4: Direct Conversation and Appliance UX](MILESTONE_4.md) — active.

The milestone files own current scope and acceptance. This roadmap describes
future direction only; it does not authorize implementation.

## Milestone 4: Direct Conversation and Appliance UX

Deliberate tap-to-talk uses the existing Louis/XC OpenClaw conversation. The
recognized question and final answer remain visible in Telegram; the robot
performs a short attention movement, waits for physical settle, and then speaks
the answer without the pending-offer consent cycle.

The device also adds a full-screen avatar with compact status icons, swipe-up
volume settings, visible battery and charging state, and restrained
power-aware standby. See [`MILESTONE_4.md`](MILESTONE_4.md) for the exact
contract and exit criteria.

## Milestone 5: Ambient Life and Senses

Sparse deterministic character behavior and explicitly requested sensing may
make the body richer without always-on surveillance, free-form motion, or
constant servo activity.

Potential scope:

- sparse deterministic blinking and gaze shifts;
- subtle breathing or posture changes within reviewed servo limits;
- local touch reactions that do not create agent messages;
- user-requested single-frame vision rather than continuous observation;
- strict activity budgets that prevent repetitive motion; and
- immediate suspension during active interaction or low-power states.

## Milestone 6: Stick S3 Continuity

Connect StackChan and XC Buddy as two physical channels for the same OpenClaw
identity without turning either device into a second agent.

Potential scope:

- StackChan remains the public home body;
- Stick S3 remains a portable, private channel;
- OpenClaw owns shared identity, memory, and judgment;
- cross-device deduplication prevents repeated notifications; and
- private content never moves to the public body without explicit consent.

This milestone requires an explicit cross-repository protocol and deployment
plan. XC Body must not modify `xc-buddy` without separate authorization.

## Deferred Until Proven Valuable

- Always-on microphone or camera.
- Face recognition.
- Home Assistant integration.
- Free-form model-generated movement.
- Multiple robots.
- Rich simulated emotion models.
- Long autonomous monologues or generated dances.
- A new mobile companion application.
