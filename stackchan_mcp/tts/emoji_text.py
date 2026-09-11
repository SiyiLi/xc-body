"""Emoji handling for TTS engines that do not support style cues."""

from __future__ import annotations

import re


_EMOJI_BASE = (
    "["
    "\u00a9\u00ae"
    "\u2600-\u27bf"
    "\U0001f000-\U0001faff"
    "]"
)
_EMOJI_MODIFIER = "[\ufe0f\U0001f3fb-\U0001f3ff]*"
_EMOJI_SEQUENCE_RE = re.compile(
    f"(?:[0-9#*]\ufe0f?\u20e3|"
    f"{_EMOJI_BASE}{_EMOJI_MODIFIER}"
    f"(?:\u200d{_EMOJI_BASE}{_EMOJI_MODIFIER})*)"
)
_EMOJI_RESIDUE_RE = re.compile("[\ufe0f\u200d\U0001f3fb-\U0001f3ff\u20e3]")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_emoji_for_plain_tts(text: str) -> str:
    """Remove emoji for engines that do not interpret them as style cues."""
    if _EMOJI_SEQUENCE_RE.search(text) is None:
        return text

    stripped = _EMOJI_SEQUENCE_RE.sub(" ", text)
    stripped = _EMOJI_RESIDUE_RE.sub(" ", stripped)
    return _WHITESPACE_RE.sub(" ", stripped).strip()
