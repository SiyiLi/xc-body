# MCP boundary

Milestone 1 exposes exactly one OpenClaw-facing semantic tool, `embody`, through
`semantic_tool.py`. Its descriptor loads the tracked
`contracts/embodiment-intent.schema.json` contract directly, so callers can
supply only `version`, `intent`, and optional null-only `speech`. The
transport-neutral handler validates the tool name and arguments before device
work, delegates execution to `gateway.embodiment.embody` through an injected
`DevicePort`, and returns only the deterministic semantic outcome. Adapter
preflight must verify every mapped face as human-visible before this handler can
return success.

This directory intentionally has no `__init__.py`: the Python MCP SDK uses the
top-level package name `mcp`, and the local boundary must not shadow it. A
semantic server integration may load `semantic_tool.py` by path and register
its descriptor and handler with the SDK.

No OpenClaw-facing semantic MCP server is implemented or running here. The
upstream client runner lives under `gateway/` and imports the deployment MCP SDK
only when executed. Tests use an injected callable and establish no network or
hardware connectivity. Request validation and mandatory return to idle remain
owned by `gateway/`; recipes, calibration, and low-level translation remain
owned by `stackchan/`.
