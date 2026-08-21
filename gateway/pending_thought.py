"""Milestone 2 knock-wait-tell state machine."""

from __future__ import annotations

import base64
import binascii
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from threading import RLock
from typing import Literal, Protocol, cast

Decision = Literal["ignore", "remember", "offer"]
_ALLOWED_FIELDS = frozenset(
    ("version", "thought_id", "decision", "audio_base64")
)
_REQUIRED_FIELDS = frozenset(("version", "thought_id", "decision"))
_DECISIONS = frozenset(("ignore", "remember", "offer"))
_THOUGHT_ID_FIRST_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)
_THOUGHT_ID_CHARS = _THOUGHT_ID_FIRST_CHARS | frozenset("._:-")
_MAX_RECORDED_OUTCOMES = 1024
_MAX_AUDIO_BASE64_CHARS = 1_048_576
_MAX_OPUS_PACKETS = 4096
_MAX_OPUS_PACKET_BYTES = 1275
_REQUIRED_OPUS_PACKET_DURATION_MS = 60


class PendingThoughtError(ValueError):
    """A pending-thought request or state transition is invalid."""


class PendingOfferExistsError(PendingThoughtError):
    """A second offer cannot replace the thought awaiting acknowledgment."""


@dataclass(frozen=True)
class PendingThought:
    version: Literal["v1"]
    thought_id: str
    decision: Decision
    audio_base64: str | None = None


@dataclass(frozen=True)
class ThoughtOutcome:
    thought_id: str
    decision: Decision
    state: Literal["ignored", "remembered", "waiting", "told"]


class KnockPort(Protocol):
    def knock(self, thought_id: str) -> None:
        """Offer one thought silently without receiving prepared audio."""


class TellPort(Protocol):
    def tell(self, thought_id: str, audio_base64: str) -> None:
        """Play prepared audio using the ID as the endpoint idempotency key."""


def decode_prepared_audio(audio_base64: str) -> bytes:
    """Decode framing and byte-verifiable Opus profile facts."""

    try:
        payload = base64.b64decode(audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PendingThoughtError("prepared audio is not valid base64") from exc

    offset = 0
    packet_count = 0
    while offset < len(payload):
        if len(payload) - offset < 2:
            raise PendingThoughtError(
                "prepared audio has trailing bytes after the last packet"
            )
        packet_size = int.from_bytes(payload[offset : offset + 2], "big")
        offset += 2
        if packet_size == 0:
            raise PendingThoughtError(
                "prepared audio contains a zero-length Opus packet"
            )
        if packet_size > _MAX_OPUS_PACKET_BYTES:
            raise PendingThoughtError(
                "prepared audio contains an oversized Opus packet"
            )
        if len(payload) - offset < packet_size:
            raise PendingThoughtError(
                "prepared audio contains an incomplete Opus packet"
            )
        packet = payload[offset : offset + packet_size]
        _validate_opus_packet_profile(packet)
        offset += packet_size
        packet_count += 1
        if packet_count > _MAX_OPUS_PACKETS:
            raise PendingThoughtError(
                "prepared audio contains too many Opus packets"
            )
    if packet_count == 0:
        raise PendingThoughtError("prepared audio contains no Opus packets")
    return payload


def _validate_opus_packet_profile(packet: bytes) -> None:
    """Check the mono flag and duration exposed by an Opus packet TOC.

    The raw packet does not carry the producer's PCM input sample rate, and
    checking the TOC is not a substitute for decoding the packet.
    """

    if packet[0] & 0x04:
        raise PendingThoughtError(
            "prepared audio Opus packets must declare mono"
        )
    duration_ms = _opus_packet_duration_ms(packet)
    if duration_ms != _REQUIRED_OPUS_PACKET_DURATION_MS:
        raise PendingThoughtError(
            "prepared audio Opus packets must contain exactly 60 ms "
            f"(got {duration_ms:g} ms)"
        )


def _opus_packet_duration_ms(packet: bytes) -> float:
    """Return duration from the Opus TOC without claiming decodability."""

    toc = packet[0]
    config = toc >> 3
    if config < 12:
        frame_duration_ms = (10, 20, 40, 60)[config & 0x03]
    elif config < 16:
        frame_duration_ms = (10, 20)[config & 0x01]
    else:
        frame_duration_ms = (2.5, 5, 10, 20)[config & 0x03]

    frame_code = toc & 0x03
    if frame_code == 0:
        frame_count = 1
    elif frame_code in (1, 2):
        frame_count = 2
    else:
        if len(packet) < 2:
            raise PendingThoughtError(
                "prepared audio Opus packet omits its frame count"
            )
        frame_count = packet[1] & 0x3F
        if frame_count == 0:
            raise PendingThoughtError(
                "prepared audio Opus packet has zero frames"
            )
    return frame_duration_ms * frame_count


def is_head_acknowledgment(event: Mapping[str, object]) -> bool:
    """Return whether one event is a deliberate supported head gesture."""

    head_gestures = (
        ("tap", "head_pat"),
        ("stroke", "head_stroke"),
    )
    return event.get("event_type") == "touch" and (
        event.get("subtype"), event.get("action")
    ) in head_gestures


def validate_thought_id(value: object) -> str:
    """Return one contract-valid duplicate-suppression identifier."""

    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or value[0] not in _THOUGHT_ID_FIRST_CHARS
        or any(character not in _THOUGHT_ID_CHARS for character in value)
    ):
        raise PendingThoughtError("thought_id has an invalid format")
    return value


def parse_pending_thought(payload: Mapping[str, object]) -> PendingThought:
    if not isinstance(payload, Mapping):
        raise PendingThoughtError("pending thought must be a JSON object")
    fields = set(payload)
    if not all(isinstance(field, str) for field in fields):
        raise PendingThoughtError("pending-thought field names must be strings")
    extra = fields - _ALLOWED_FIELDS
    if extra:
        raise PendingThoughtError(
            "unexpected pending-thought field(s): "
            + ", ".join(sorted(cast(set[str], extra)))
        )
    missing = _REQUIRED_FIELDS - fields
    if missing:
        raise PendingThoughtError(
            "missing pending-thought field(s): " + ", ".join(sorted(missing))
        )
    if payload["version"] != "v1":
        raise PendingThoughtError(
            f"unsupported pending-thought version: {payload['version']!r}"
        )
    thought_id = validate_thought_id(payload["thought_id"])
    decision = payload["decision"]
    if not isinstance(decision, str) or decision not in _DECISIONS:
        raise PendingThoughtError(f"unsupported decision: {decision!r}")
    audio_base64 = payload.get("audio_base64")
    if decision == "offer":
        if (
            not isinstance(audio_base64, str)
            or not 1 <= len(audio_base64) <= _MAX_AUDIO_BASE64_CHARS
        ):
            raise PendingThoughtError(
                "offer requires non-empty audio_base64 of at most 1048576 chars"
            )
        audio_base64 = audio_base64.strip()
        if not audio_base64:
            raise PendingThoughtError(
                "offer requires non-empty audio_base64 of at most 1048576 chars"
            )
        decode_prepared_audio(audio_base64)
    elif "audio_base64" in payload:
        raise PendingThoughtError(
            "audio_base64 is permitted only when decision is 'offer'"
        )
    return PendingThought(
        version="v1",
        thought_id=thought_id,
        decision=cast(Decision, decision),
        audio_base64=audio_base64,
    )


class KnockWaitTell:
    """Hold one offer; suppress IDs retained in bounded process memory."""

    def __init__(self, knock_port: KnockPort, tell_port: TellPort):
        self._knock_port = knock_port
        self._tell_port = tell_port
        self._outcomes: OrderedDict[str, ThoughtOutcome] = OrderedDict()
        self._pending: PendingThought | None = None
        self._lock = RLock()

    @property
    def pending_thought_id(self) -> str | None:
        with self._lock:
            return self._pending.thought_id if self._pending else None

    def submit(self, payload: Mapping[str, object]) -> ThoughtOutcome:
        thought = parse_pending_thought(payload)
        with self._lock:
            previous = self._outcomes.get(thought.thought_id)
            if previous is not None:
                return previous
            if thought.decision == "ignore":
                return self._record(thought, "ignored")
            if thought.decision == "remember":
                return self._record(thought, "remembered")
            if self._pending is not None:
                raise PendingOfferExistsError(
                    f"thought {self._pending.thought_id!r} "
                    "is awaiting acknowledgment"
                )
            self._pending = thought
            try:
                self._knock_port.knock(thought.thought_id)
            except Exception:
                self._pending = None
                raise
            return self._record(thought, "waiting")

    def acknowledge_head_gesture(self) -> ThoughtOutcome | None:
        with self._lock:
            thought = self._pending
            if thought is None:
                return None
            if thought.audio_base64 is None:
                raise RuntimeError("pending offer has no prepared audio")
            self._tell_port.tell(thought.thought_id, thought.audio_base64)
            self._pending = None
            return self._record(thought, "told")

    def handle_stackchan_event(
        self, event: Mapping[str, object]
    ) -> ThoughtOutcome | None:
        """Acknowledge a deliberate tap or short stroke on the head."""

        if not isinstance(event, Mapping):
            raise PendingThoughtError("StackChan event must be an object")
        if not is_head_acknowledgment(event):
            return None
        return self.acknowledge_head_gesture()

    def _record(
        self,
        thought: PendingThought,
        state: Literal["ignored", "remembered", "waiting", "told"],
    ) -> ThoughtOutcome:
        outcome = ThoughtOutcome(
            thought_id=thought.thought_id,
            decision=thought.decision,
            state=state,
        )
        self._outcomes[thought.thought_id] = outcome
        while len(self._outcomes) > _MAX_RECORDED_OUTCOMES:
            pending_id = self._pending.thought_id if self._pending else None
            oldest_id = next(iter(self._outcomes))
            if oldest_id == pending_id:
                self._outcomes.move_to_end(oldest_id)
                continue
            self._outcomes.popitem(last=False)
        return outcome
