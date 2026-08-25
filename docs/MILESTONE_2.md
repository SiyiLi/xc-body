# Milestone 2: Knock, Wait, Tell

## Objective

Offer one meaningful background result silently and play its prepared audio
only after deliberate physical acknowledgment.

## Accepted Behavior

- The decision contract accepts only `ignore`, `remember`, and `offer`.
- `ignore` and `remember` produce no physical action.
- Only one offer may wait at a time.
- An offer performs one deterministic silent knock that receives no audio.
- A CoreS3 head pat or head stroke acknowledges the waiting offer.
- Successful acknowledgment plays validated prepared Opus audio.
- A tell failure keeps the offer pending and reports the failure.
- Retained recent IDs suppress duplicate work within the running process.
- Process restart or recent-ID eviction may allow a replay.
- No text crosses the gateway or reaches an upstream `say` tool.

The tracked contract is `contracts/pending-thought.schema.json`.
Milestone 3 owns expiry, reconnect recovery, retries, and supervisor behavior.

## Physical Acceptance

Milestone 2 is complete. OpenClaw selected an offer and supplied the spoken
Chinese message. The robot knocked once, a physical head stroke acknowledged
the offer, playback completed, and the speech was judged clear.

This acceptance covers the running-process behavior above. It does not claim
restart persistence or cross-session recovery.
