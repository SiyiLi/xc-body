#!/usr/bin/env python3
"""Check first-party Python and Markdown line lengths."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "__pycache__"}
IMPORTED_FIRMWARE_PREFIX = ROOT / "firmware"
MARKDOWN_EXEMPT_CHARS = frozenset("┌┐└┘│─▶▼▲")


def markdown_line_is_exempt(line: str) -> bool:
    stripped = line.lstrip()
    return (
        "http://" in line
        or "https://" in line
        or stripped.startswith(("|", "```"))
        or any(character in line for character in MARKDOWN_EXEMPT_CHARS)
    )


def main() -> int:
    failures: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if path.is_relative_to(IMPORTED_FIRMWARE_PREFIX):
            continue
        if path.suffix not in {".py", ".md"}:
            continue

        limit = 88 if path.suffix == ".py" else 80
        for line_number, line in enumerate(path.read_text().splitlines(), 1):
            if len(line) <= limit:
                continue
            if path.suffix == ".md" and markdown_line_is_exempt(line):
                continue
            relative_path = path.relative_to(ROOT)
            failures.append(
                f"{relative_path}:{line_number}: {len(line)} > {limit}"
            )

    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
