"""Synchronous client wrapper for the upstream StackChan MCP tool surface."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

ToolCaller = Callable[[str, Mapping[str, object]], object]
_MISSING = object()


class UpstreamClientError(RuntimeError):
    """An upstream MCP operation failed or returned an invalid result."""

    def __init__(self, operation: str, message: str):
        self.operation = operation
        super().__init__(f"{operation}: {message}")


class UpstreamStackChanClient:
    """Implement StackChanClient through one injected synchronous tool caller."""

    def __init__(self, call_tool: ToolCaller):
        self._call_tool = call_tool

    def get_status(self) -> Mapping[str, object]:
        payload = self._call("get_status", {})
        connected = self._connected_value(payload)
        if connected is None:
            raise UpstreamClientError(
                "get_status", "result did not contain a boolean connection state"
            )
        normalized = dict(payload)
        normalized["connected"] = connected
        return normalized

    def set_avatar(self, face: str) -> Mapping[str, object]:
        return self._call("set_avatar", {"face": face})

    def move_head(
        self, yaw: float, pitch: float, speed: float
    ) -> Mapping[str, object]:
        return self._call(
            "move_head",
            {"yaw": yaw, "pitch": pitch, "speed": speed},
        )

    def _call(
        self, operation: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        try:
            result = self._call_tool(operation, arguments)
        except Exception as exc:
            raise UpstreamClientError(operation, str(exc)) from exc
        if self._field(result, "isError", "is_error") is True:
            message = self._content_message(result) or "upstream tool failed"
            raise UpstreamClientError(operation, message)
        payload = self._payload(operation, result)
        if payload.get("ok") is False or payload.get("success") is False:
            message = payload.get("error") or payload.get("message")
            raise UpstreamClientError(operation, str(message or "tool failed"))
        return payload

    @classmethod
    def _payload(
        cls, operation: str, result: object
    ) -> Mapping[str, object]:
        structured = cls._field(
            result, "structuredContent", "structured_content"
        )
        if isinstance(structured, Mapping):
            return structured

        content = cls._field(result, "content")
        if content is not _MISSING:
            texts = cls._text_blocks(content)
            for text in texts:
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, Mapping):
                    return decoded
            if texts and operation != "get_status":
                return {"ok": True, "message": "\n".join(texts)}
            raise UpstreamClientError(
                operation, "tool result did not contain a JSON object"
            )

        if isinstance(result, Mapping):
            return result
        raise UpstreamClientError(operation, "tool returned an unsupported result")

    @classmethod
    def _content_message(cls, result: object) -> str:
        content = cls._field(result, "content")
        if content is _MISSING:
            return ""
        return "\n".join(cls._text_blocks(content))

    @classmethod
    def _text_blocks(cls, content: object) -> list[str]:
        if not isinstance(content, Sequence) or isinstance(
            content, (str, bytes, bytearray)
        ):
            return []
        texts: list[str] = []
        for block in content:
            text = cls._field(block, "text")
            if isinstance(text, str):
                texts.append(text)
        return texts

    @classmethod
    def _connected_value(cls, payload: Mapping[str, object]) -> bool | None:
        candidates: list[Mapping[str, object]] = [payload]
        for key in ("data", "device", "status"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                candidates.append(nested)
        for candidate in candidates:
            for key in ("connected", "device_connected", "is_connected"):
                value = candidate.get(key)
                if isinstance(value, bool):
                    return value
        return None

    @staticmethod
    def _field(value: object, *names: str) -> Any:
        for name in names:
            if isinstance(value, Mapping) and name in value:
                return value[name]
            attribute = getattr(value, name, _MISSING)
            if attribute is not _MISSING:
                return attribute
        return _MISSING
