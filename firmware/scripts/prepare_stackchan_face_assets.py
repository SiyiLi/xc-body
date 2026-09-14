#!/usr/bin/env python3
"""Generate canonical XC Body face GIFs from tracked source GIFs."""

from __future__ import annotations

import argparse
import bisect
import io
import sys
from pathlib import Path

from PIL import Image, __version__ as pillow_version


EXPECTED_PILLOW_VERSION = "11.3.0"
FACE_SIZE = (320, 240)
CANONICAL_BACKGROUND = (6, 16, 25)
FACE_ASSETS = (
    "expression-agree.gif",
    "expression-concerned.gif",
    "expression-curious.gif",
    "expression-embarrassed.gif",
    "expression-mischievous.gif",
    "expression-pleased.gif",
    "expression-surprised.gif",
    "idle.gif",
    "listening.gif",
    "speaking.gif",
)
ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "main/boards/stackchan/face-assets/source"
OUTPUT_DIR = ROOT / "main/boards/stackchan/assets"


class FaceAssetError(RuntimeError):
    """The tracked face source or generated face GIF is invalid."""


def _source_frames(
    path: Path,
) -> tuple[list[Image.Image], list[int], int, tuple[int, int, int]]:
    with Image.open(path) as source:
        if source.size != FACE_SIZE:
            raise FaceAssetError(f"{path.name}: expected {FACE_SIZE}")
        loop = source.info.get("loop", 0)
        frames: list[Image.Image] = []
        durations: list[int] = []
        source.seek(0)
        background = source.convert("RGB").getpixel((0, 0))
        corners = (
            (0, 0),
            (FACE_SIZE[0] - 1, 0),
            (0, FACE_SIZE[1] - 1),
            (FACE_SIZE[0] - 1, FACE_SIZE[1] - 1),
        )
        if any(
            source.convert("RGB").getpixel(point) != background
            for point in corners
        ):
            raise FaceAssetError(
                f"{path.name}: first frame has no flat background"
            )
        for index in range(source.n_frames):
            source.seek(index)
            frame = source.convert("RGB").copy()
            duration = source.info.get("duration")
            if not isinstance(duration, int) or duration <= 0:
                raise FaceAssetError(
                    f"{path.name}: frame {index} has invalid timing"
                )
            frames.append(frame)
            durations.append(duration)
    return frames, durations, loop, background


def _palette_frames(
    frames: list[Image.Image],
    background: tuple[int, int, int],
) -> list[Image.Image]:
    colors = sorted(
        {
            pixel
            for frame in frames
            for pixel in frame.getdata()
            if pixel not in {background, CANONICAL_BACKGROUND}
        }
    )
    if len(colors) > 255:
        raise FaceAssetError("face artwork needs more than 255 colors")
    color_indexes = {color: index + 1 for index, color in enumerate(colors)}
    palette = list(CANONICAL_BACKGROUND)
    for color in colors:
        palette.extend(color)
    palette.extend([0] * (768 - len(palette)))

    output = []
    for source in frames:
        frame = Image.new("P", FACE_SIZE)
        frame.putpalette(palette)
        frame.putdata([
            0
            if pixel in {background, CANONICAL_BACKGROUND}
            else color_indexes[pixel]
            for pixel in source.getdata()
        ])
        output.append(frame)
    return output


def _encode_asset(path: Path) -> bytes:
    frames, durations, loop, background = _source_frames(path)
    output_frames = _palette_frames(frames, background)
    encoded = io.BytesIO()
    output_frames[0].save(
        encoded,
        format="GIF",
        save_all=True,
        append_images=output_frames[1:],
        duration=durations,
        loop=loop,
        disposal=1,
        optimize=True,
    )
    payload = encoded.getvalue()
    _validate_output(path.name, payload, frames, durations, background)
    return payload


def _output_timeline(payload: bytes) -> tuple[list[int], list[Image.Image]]:
    ends = []
    frames = []
    elapsed = 0
    with Image.open(io.BytesIO(payload)) as output:
        if output.size != FACE_SIZE:
            raise FaceAssetError("generated face has the wrong size")
        for index in range(output.n_frames):
            output.seek(index)
            if output.disposal_method != 1:
                raise FaceAssetError("generated face has invalid disposal")
            duration = output.info.get("duration")
            if not isinstance(duration, int) or duration <= 0:
                raise FaceAssetError("generated face has invalid timing")
            elapsed += duration
            ends.append(elapsed)
            frames.append(output.convert("RGB").copy())
    return ends, frames


def _expected_frame(
    frame: Image.Image,
    background: tuple[int, int, int],
) -> Image.Image:
    expected = Image.new("RGB", FACE_SIZE)
    expected.putdata([
        CANONICAL_BACKGROUND
        if pixel == background
        else pixel
        for pixel in frame.getdata()
    ])
    return expected


def _validate_output(
    name: str,
    payload: bytes,
    source_frames: list[Image.Image],
    durations: list[int],
    background: tuple[int, int, int],
) -> None:
    ends, output_frames = _output_timeline(payload)
    if ends[-1] != sum(durations):
        raise FaceAssetError(f"{name}: generated duration changed")
    if output_frames[0].getpixel((0, 0)) != CANONICAL_BACKGROUND:
        raise FaceAssetError(f"{name}: generated background is not canonical")

    started_at = 0
    for source, duration in zip(source_frames, durations):
        output_index = bisect.bisect_right(ends, started_at)
        if output_frames[output_index].tobytes() != _expected_frame(
            source, background
        ).tobytes():
            raise FaceAssetError(f"{name}: generated artwork changed")
        started_at += duration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="replace packaged face GIFs with generated assets",
    )
    args = parser.parse_args()
    if pillow_version != EXPECTED_PILLOW_VERSION:
        raise FaceAssetError(
            "Pillow "
            f"{EXPECTED_PILLOW_VERSION} is required; found {pillow_version}"
        )

    stale = []
    for name in FACE_ASSETS:
        payload = _encode_asset(SOURCE_DIR / name)
        destination = OUTPUT_DIR / name
        if args.write:
            destination.write_bytes(payload)
        elif not destination.is_file() or destination.read_bytes() != payload:
            stale.append(name)
    if stale:
        print("stale face assets: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FaceAssetError as error:
        print(f"face asset error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
