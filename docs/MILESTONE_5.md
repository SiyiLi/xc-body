# Milestone 5: Expression and Presence

## Objective

Replace the legacy layered avatar with one deterministic expression renderer
while preserving XC Body's established conversation, offer, and power flows.
The renderer combines reviewed GIF animation with firmware-owned head motion
and exact safe return.

## Current Status

The implementation scope is complete. The seven motor recipes were physically
calibrated on source commit `e212a1c`. That evidence remains useful, but the
replacement renderer and its runtime integration require the versioned
end-to-end hardware paths below before Milestone 5 receives physical
acceptance. No complete firmware, gateway, OpenClaw, and source combination is
claimed as accepted yet.

## Expression Vocabulary

The named recipes remain:

| Expression | Intended use |
| --- | --- |
| `agree` | agreement through one restrained nod |
| `pleased` | good news, thanks, and warmth |
| `curious` | questions, inquiry, and uncertainty |
| `concerned` | bad news, caution, and empathy |
| `surprised` | genuinely unexpected information |
| `embarrassed` | mistakes and mild self-consciousness |
| `mischievous` | playful cunning and knowing humor |

Idle presence is the looping `idle.gif` asset; it is not a separate motor
recipe. The legacy `AvatarSet`, layered faces, eyes, mouth shapes, blink state,
lip-sync renderer, download route, and checksum lifecycle do not remain as a
fallback.

## Recipe Contract

Every saved recipe uses schema 2:

```json
{
  "schema_version": 2,
  "animations": ["curious"],
  "steps": [{
    "type": "curve",
    "start": [0, 43],
    "via": [[0, 45], [0, 45]],
    "end": [0, 43],
    "duration_ms": 500
  }]
}
```

`animations` is an ordered array so a later schema can play multiple GIFs in
sequence. The current implementation deliberately accepts exactly one entry
and keeps one internal animation string. It does not implement sequencing yet.

`touch.json` uses this same schema and defaults to the `pleased` GIF plus the
existing head-shake movement. It can be previewed and replaced through the
existing USB expression commands under the name `touch`; changing its one
animation entry later changes the face without adding a touch-only renderer.

Firmware validates step count and duration, servo ranges, curve continuity,
exact idle start and return, and sampled velocity before movement. The shared
motion driver and physical owner remain the only execution path. A named
expression also requires its named GIF: a missing or invalid expression asset
fails before head movement. This is a critical assets integrity failure; direct
attention, offer knocks, and touch reactions remain unavailable until a valid
assets package repairs it. A blank face is not a substitute for an expression.

## Runtime Behavior

- Compound transcription routes directly to one of the seven named silent
  expressions when that is a natural and complete response. Questions,
  requests requiring action or explanation, and uncertain cases run the agent
  normally.
- Every completed direct answer is projected to select one of the seven named
  expressions from its full meaning. A short answer keeps its exact speech; a
  long or formatted answer is also projected into bounded speech. Interaction
  runs the selected expression through safe return before starting the
  unchanged speech path.
- Background projection selects `skip` or bounded speech plus one non-idle
  expression. An offer prepares audio, runs that expression once through safe
  return, then enters the unchanged wait-for-touch state. The VM keeps the idle
  screensaver hidden while the offer waits.
- While direct attention or its speech is active, a head tap or stroke is
  ignored. After it completes, the next gesture runs the locally stored
  `touch` recipe. Only that reaction's successful safe return emits its
  existing event. The VM starts prepared playback when an offer is pending and
  discards the event otherwise. A rejected or failed touch reaction has no
  offer or audio effect.
- While recording, firmware plays `listening.gif` when that optional asset is
  available. It never starts a motor recipe; a missing or invalid GIF leaves
  the static idle face visible and cannot block recording.
- `speaking.gif` loops only for actual audio playback, with no head motion or
  blink. Missing or invalid speaking art cannot block speech. TTS stop restores
  idle presence.
- The private `perform_expression` gateway tool accepts `idle` or one of the
  seven expression names for internal execution. Public voice and summary
  requests carry one of the seven named expressions; model output cannot
  select `idle`. Internally, `idle` restores the safe pose, while named
  expressions use the same firmware runner. No boundary exposes motor
  parameters.

## Display Behavior

- The application view owns the expression and application status bar. The
  configuration and OTA view owns the generic system content and status row.
  Switching views hides the complete inactive pair; their layers never mix.
- The deterministic asset generator gives every face GIF the same canonical
  opaque background and preserves efficient delta frames. A transition redraws
  the complete face once; later frames redraw only their changed regions.
- XC Body does not construct generic emoji widgets. A blank face is the
  fallback only for a display-only state; it never substitutes for a named
  expression whose GIF is missing or invalid.
- Configuration, activation, maintenance, and upgrading never show a face.
- The first idle face is installed only after the device reaches `Idle`, so the
  face area remains blank during normal boot.
- After 60 seconds, the existing clock and weather screen may cover idle.
  The first LCD touch, right-side touch, or head touch only dismisses that
  screen and restores idle presence; a later interaction performs its normal
  action.
- StackChan charging uses the ordinary battery-level glyph in green from its
  first charging render. It never shows the lightning-bolt glyph.
- An accepted USB reboot or OTA request reserves maintenance first, hides the
  face, and exposes the standard maintenance UI before acknowledgment. The
  asynchronous operation consumes that same reservation.

## USB Calibration

The existing USB commands preview, save, show, and reset both the seven named
recipes and `touch`. `expression-recipes/touch.json` is both the built-in
default and the checked-in calibration input for the ordinary `touch` recipe.
Preview returns immediate admission and remains transient; the firmware runner
executes an admitted recipe independently. Save persists the validated recipe
in NVS and overrides the built-in touch default.
Routine app OTA preserves NVS. A stored schema-1 recipe for one of the original
seven expressions is migrated in memory to the equivalent single-animation
schema so previously approved motor calibration is retained.

USB remains the only boundary for changing recipes. Gateway and OpenClaw calls
can choose only a saved semantic name.

## Acceptance Paths

Before this candidate is accepted, test these complete paths on hardware and
record exact firmware, gateway, OpenClaw, and source versions:

1. boot -> configuration or activation -> idle face, with no `待命` flash;
2. screensaver -> first LCD, right-side, and head touch -> idle only;
3. charging transition -> green level glyph with no lightning glyph;
4. accepted USB OTA -> face hidden -> maintenance UI -> update;
5. direct request -> listening -> selected expression -> safe return ->
   speaking -> idle;
   head touch during attention or speech has no effect;
6. background offer -> selected expression -> safe return -> wait -> touch ->
   pleased and shake -> safe return -> prepared speech;
7. tap and stroke with no pending offer -> pleased and shake -> safe return;
   no speech; and
8. USB preview and persistence of `touch.json` with no second motion runner.
9. expression-only utterance -> no agent run -> saved firmware expression ->
   safe return -> no speech; verify both an explicit display request and a
   self-contained social or emotional remark.

## Out of Scope

This change does not add combined GIF playback, model-generated motion, sparse
idle gaze or posture changes, camera input, constant servo activity, or a
global activity framework. Milestone 6 owns future ambient motion and camera
work as one perception-guided presence feature.
