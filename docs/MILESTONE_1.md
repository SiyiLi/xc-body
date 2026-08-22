# Milestone 1: OpenClaw Gets a Body

## Objective

Let OpenClaw request a semantic intention and have StackChan present it
deterministically, safely, and with an automatic return to idle.

## Accepted Boundary

- OpenClaw submits a versioned semantic intention, never raw servo or animation
  controls.
- XC Body validates the full recipe and calibration before device work.
- Deterministic code owns faces, movement, speed, duration, and idle return.
- Every expressive recipe attempts idle return even after a device failure.
- Unsupported or incompletely calibrated intentions fail before movement.
- Device unavailability and command failures are reported honestly.
- Visible expression requires physical evidence; command success is not enough.

The tracked contract is `contracts/embodiment-intent.schema.json`.
Its optional `speech` field accepts only JSON `null`.

## Calibrated Vocabulary

| Intention | Face | Motion | Status |
| --- | --- | --- | --- |
| `idle` | `idle` | `(0,43,30)` | accepted |
| `curious` | `thinking` | `(12,50,30)`, then idle | accepted |
| `pleased` | `happy` | not calibrated | rejected |
| `concerned` | `sad` | not calibrated | rejected |

Servo commands remain within upstream yaw `-90..90` and pitch `5..85` limits.
Visible-face verification is bound to reviewed avatar payload SHA-256
`daa35ed17a716860f0415c053dbf3d59e6e421ad50556de5ccc58cae28af36f7`.

## Physical Acceptance

Milestone 1 has historical physical acceptance for the calibrated vocabulary.
The native idle and thinking faces were visible, the curious pose was judged
clear and restrained, and the recipe returned to the exact neutral pose.

`pleased` and `concerned` remain deliberately unavailable because their motion
was not calibrated.
