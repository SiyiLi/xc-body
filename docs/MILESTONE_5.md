# Milestone 5: Expression and Presence

## Objective

Give each direct answer one restrained, content-appropriate physical
expression. An expression combines a deterministic face animation and head
movement. It may represent an emotion or a conversational gesture such as a
nod. The model selects only its name; XC Body owns every physical detail and
safe return. When no expression is appropriate, neutral becomes the robot's
sparse ambient life and local sense of touch.

## Current Status

Milestone 5 became active on 2026-09-02 after Milestone 4 received real-user
physical acceptance. The initial expression vocabulary and physical recipes
still require implementation, calibration, and real-user acceptance.

The expression-aware direct path is the primary goal. Neutral ambient presence
follows in small slices and must not delay or complicate that path.

## Initial Vocabulary

Start with six expressions that reuse the six reviewed face assets:

| Expression | Face basis | Intended use |
| --- | --- | --- |
| `agree` | idle | agreement through one restrained nod |
| `pleased` | happy | good news, thanks, and warmth |
| `curious` | thinking | inquiry, exploration, and uncertainty |
| `concerned` | sad | bad news, caution, and empathy |
| `surprised` | surprised | genuinely unexpected information |
| `embarrassed` | embarrassed | mistakes and mild self-consciousness |

Each expression needs one reviewed head recipe and exact safe return. The model
cannot choose angles, speeds, holds, intensity, or animation frames. `Neutral`
is the fallback presence rather than a seventh expression. Add a new expression
only when real use exposes a missing distinction.

## USB Expression Calibration

Each motor recipe needs fast physical iteration before it becomes production
calibration. Extend the existing CoreS3 USB maintenance channel with local
preview, save, show, and reset operations. The intended preview shape is:

```sh
scripts/stackchan_usb.py expression-preview agree \
  --move <yaw>,<pitch>,<speed>,<hold-ms> \
  --move <yaw>,<pitch>,<speed>,<hold-ms>
```

- One invocation previews one named expression and its candidate steps.
- Firmware and CLI validate the bounded step count, servo ranges, speed, hold
  duration, and total duration before movement.
- Preview uses the same face mapping, body-operation ownership, motor runner,
  interruption behavior, and safe return as the production expression.
- A busy robot rejects preview rather than interleaving it with conversation,
  offers, audio, settings, recovery, or another preview.
- Preview is transient. After physical approval, a separate USB save operation
  validates and stores that exact recipe in NVS for the named expression.
- Production expression playback reads the stored motor recipe. Face mapping,
  expression names, and execution rules remain fixed in firmware.
- Show returns the canonical stored recipe and its schema version. Reset removes
  one stored recipe and makes that expression unavailable until recalibrated.
- Stored calibration survives reboot and routine OTA, which preserve NVS.
- Missing, malformed, or incompatible calibration fails before movement and
  falls back to neutral presence for the direct turn.

USB is the calibration boundary, not a new production control surface. The
gateway, OpenClaw, and projection never receive raw motor parameters.

## Direct Projection Contract

The fixed direct projection receives the complete OpenClaw answer. OpenClaw
already owns interpreting the user's request, so the projection does not also
receive the transcript. It runs for every direct answer because every answer
needs an expression selection, then returns one strict result:

```json
{"speech":"voice-ready answer","expression":"curious"}
```

- `speech` follows the accepted direct-speech limits. A short, plain answer is
  preserved when it is already voice-friendly.
- `speech` may be `null` when OpenClaw explicitly requests expression without
  audio.
- `expression` is one of the six fixed expressions or `neutral`.
- Clear semantic fit is required for a non-neutral expression. Ambiguity
  defaults to `neutral`.
- Invalid output receives the existing bounded retry, then falls back to
  neutral expression and safe speech behavior.
- No rationale, confidence, movement parameters, or open-ended labels cross
  this boundary.

For example, OpenClaw can answer “show the embarrassed expression, no audio.”
The projection then returns `embarrassed` with `speech: null`. This keeps user
intent interpretation in OpenClaw and physical selection at the projection
boundary.

## Physical Execution

For a direct turn, a selected expression replaces the existing fixed attention
behavior. Speech preparation may overlap the deterministic expression,
preserving the current attention-and-preparation overlap. Head motion settles
before playback so servo noise cannot contaminate speech. The selected face may
remain through playback, then the recipe restores neutral presence.

A `neutral` selection requires no semantic gesture. It keeps or restores the
reviewed idle face and safe centered posture before playback.

One exclusive body operation owns the complete expression-through-playback
interval, and the expression runs exactly once for the turn.

An expression-only turn completes after the recipe safely returns; it does not
open the audio path. Background offers retain their accepted
`knock -> wait -> tell` behavior and prepared-Opus path. Milestone 5 does not
add expression selection to them.

## Neutral Ambient Life and Senses

Neutral is the robot's base presence rather than another expression. Once the
six expression recipes are stable, small deterministic behaviors may make that
presence feel alive between interactions:

- sparse gaze shifts or subtle neutral posture changes;
- brief local face-and-head reactions to touch;
- strict frequency and motion budgets, with rest as the normal state; and
- immediate suspension during conversation, pending offers, settings,
  recovery, or low-power states.

These behaviors use only local state and touch input. They do not create
OpenClaw turns, Telegram messages, autonomous semantic expressions, or camera
observations. Add and physically judge one small behavior at a time rather than
introducing a general ambient engine.

## Acceptance

Milestone 5 closes when real use finds direct answers naturally expressive and
the robot quietly present between interactions, without making it
melodramatic, repetitive, distracting, or unsafe.

## Reference Validation Scenarios

These are engineering references, not independent acceptance criteria.

- Each of the six expressions maps to one deterministic, calibrated recipe.
- `Neutral` uses the reviewed idle face and safe centered posture.
- USB preview rejects unsafe or malformed steps before movement.
- USB preview uses production ownership and returns safely after success,
  failure, cancellation, or disconnect.
- A saved recipe survives reboot and is the recipe production playback uses.
- USB show reports the exact stored recipe; reset makes it unavailable.
- Corrupt or incompatible stored calibration cannot move the robot.
- An explicit OpenClaw answer requesting an expression selects it exactly.
- An expression-only result performs no audio playback.
- Unsupported or malformed output cannot invent a motion or expression.
- Projection failure falls back to `neutral` without duplicating the turn.
- Every recipe stays within reviewed servo limits and restores the base view.
- Direct expression, speech, and pending-offer restoration remain serialized.
- Ambient behavior respects its activity budget and yields immediately to
  interaction, recovery, and low-power behavior.
- Local touch reactions create no agent turn or Telegram message.
- Background-offer behavior is unchanged.

## Explicitly Deferred

- camera input or visual observation, which belongs to Milestone 6;
- expression selection for background offers;
- autonomous semantic moods or constant movement;
- model-selected intensity, timing, or servo trajectories;
- additional expressions without evidence from real use;
- free-form model-generated movement.
