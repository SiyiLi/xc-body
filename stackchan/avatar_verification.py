"""Reviewed native-avatar identity and load-result verification."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

REVIEWED_AVATAR_SHA256 = (
    "daa35ed17a716860f0415c053dbf3d59e6e421ad50556de5ccc58cae28af36f7"
)
REVIEWED_AVATAR_CHECKSUM = f"sha256:{REVIEWED_AVATAR_SHA256}"


class AvatarVerificationError(RuntimeError):
    """The device did not confirm the exact reviewed avatar payload."""


def require_reviewed_avatar_load(result: object) -> None:
    """Fail closed unless an MCP result confirms the reviewed payload digest."""

    payload = _payload(result)
    error_flag = _result_field(result, "isError", "is_error")
    if error_flag is not _MISSING:
        if not isinstance(error_flag, bool):
            raise AvatarVerificationError(
                "native avatar restore returned an invalid MCP error flag"
            )
        if error_flag:
            error = (
                payload.get("error")
                or payload.get("message")
                or "upstream tool reported failure"
            )
            raise AvatarVerificationError(
                f"native avatar restore reported failure: {error}"
            )

    if payload.get("ok") is not True:
        error = payload.get("error") or "device did not confirm avatar loading"
        raise AvatarVerificationError(f"native avatar restore failed: {error}")
    checksum = payload.get("checksum")
    if checksum != REVIEWED_AVATAR_CHECKSUM:
        raise AvatarVerificationError(
            "native avatar restore checksum does not match the reviewed payload"
        )


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
    if isinstance(result, Mapping):
        candidates.append(result.get("structuredContent"))
    else:
        candidates.append(getattr(result, "structured_content", None))
        content = getattr(result, "content", None)
        if isinstance(content, list) and content:
            text = getattr(content[0], "text", None)
            if isinstance(text, str):
                try:
                    candidates.append(json.loads(text))
                except json.JSONDecodeError:
                    pass
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            if any(
                key in candidate for key in ("ok", "error", "checksum")
            ):
                return candidate
    raise AvatarVerificationError(
        "native avatar restore returned an invalid result"
    )
