"""Reviewed native-avatar identity and load-result verification."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

REVIEWED_AVATAR_SHA256 = (
    "daa35ed17a716860f0415c053dbf3d59e6e421ad50556de5ccc58cae28af36f7"
)
REVIEWED_AVATAR_CHECKSUM = f"sha256:{REVIEWED_AVATAR_SHA256}"


class AvatarVerificationError(RuntimeError):
    """The device did not confirm the exact reviewed avatar payload."""


def require_reviewed_avatar_load(result: object) -> None:
    """Fail closed unless an MCP result confirms the reviewed payload digest."""

    error_flag = _result_field(result, "isError", "is_error")
    if error_flag is not _MISSING:
        if not isinstance(error_flag, bool):
            raise AvatarVerificationError(
                "native avatar restore returned an invalid MCP error flag"
            )
        if error_flag:
            raise AvatarVerificationError(
                "native avatar restore reported failure: "
                f"{_content_message(result)}"
            )

    payload = _payload(result)
    if payload.get("ok") is not True:
        error = payload.get("error") or "device did not confirm avatar loading"
        raise AvatarVerificationError(f"native avatar restore failed: {error}")
    checksum = payload.get("checksum")
    if checksum != REVIEWED_AVATAR_CHECKSUM:
        raise AvatarVerificationError(
            "native avatar restore checksum does not match the reviewed payload"
        )


def require_ready_device_session(result: object) -> str:
    """Return the connected, initialized device session or fail closed."""

    payload = _payload(result)
    session_id = payload.get("session_id")
    if (
        payload.get("connected") is not True
        or payload.get("initialized") is not True
        or not isinstance(session_id, str)
        or not session_id
    ):
        raise AvatarVerificationError(
            "StackChan device is not connected and initialized"
        )
    return session_id


_MISSING = object()


def _result_field(result: object, *names: str) -> object:
    for name in names:
        if isinstance(result, Mapping) and name in result:
            return result[name]
        value = getattr(result, name, _MISSING)
        if value is not _MISSING:
            return value
    return _MISSING


def _payload(result: object) -> Mapping[str, Any]:
    candidates = [result]
    candidates.append(
        _result_field(result, "structuredContent", "structured_content")
    )
    content = _result_field(result, "content")
    if isinstance(content, Sequence) and not isinstance(
        content, (str, bytes, bytearray)
    ):
        for block in content:
            text = _result_field(block, "text")
            if not isinstance(text, str):
                continue
            try:
                candidates.append(json.loads(text))
            except json.JSONDecodeError:
                continue
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            if any(
                key in candidate
                for key in (
                    "ok",
                    "error",
                    "checksum",
                    "connected",
                    "initialized",
                    "session_id",
                )
            ):
                return candidate
    raise AvatarVerificationError(
        "native avatar restore returned an invalid result"
    )


def _content_message(result: object) -> str:
    content = _result_field(result, "content")
    if isinstance(content, Sequence) and not isinstance(
        content, (str, bytes, bytearray)
    ):
        for block in content:
            text = _result_field(block, "text")
            if isinstance(text, str) and text:
                return text
    return "upstream tool reported failure"
