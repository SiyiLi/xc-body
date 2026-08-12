"""Deterministic layered avatar assets for the upstream StackChan runtime."""

from __future__ import annotations

import json
import struct
import zlib
from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from pathlib import Path

DESIGN_WIDTH = 160
DESIGN_HEIGHT = 120
OUTPUT_SCALE = 2
SUPERSAMPLE = 4
DRAW_SCALE = OUTPUT_SCALE * SUPERSAMPLE
WIDTH = DESIGN_WIDTH * OUTPUT_SCALE
HEIGHT = DESIGN_HEIGHT * OUTPUT_SCALE
BYTES_PER_PIXEL = 2
FRAME_BYTES = WIDTH * HEIGHT * BYTES_PER_PIXEL
FRAME_ORDER = (
    "face_idle",
    "face_happy",
    "face_thinking",
    "face_sad",
    "face_surprised",
    "face_embarrassed",
    "eye_open",
    "eye_half",
    "eye_closed",
    "mouth_closed",
    "mouth_half",
    "mouth_open",
    "mouth_e",
    "mouth_u",
)
FACE_FRAME_COUNT = 6
TOTAL_BYTES = len(FRAME_ORDER) * FRAME_BYTES
MODE = "layered-320x240"
MANIFEST_VERSION = "xc-body-avatar-v1"
PIXEL_FORMAT = "RGB565-LE"

# Large enough to reject cosmetic hash-only differences while allowing compact
# expression symbols. Generated face pairs currently exceed this lower bound.
MIN_FACE_DIFFERENCE_PIXELS = 300
MIN_ARTWORK_PIXELS = 2_000

PREVIEW_SCALE = 1
PREVIEW_COLUMNS = 4
PREVIEW_ROWS = 4
PREVIEW_MARGIN = 8
PREVIEW_GAP = 4
PREVIEW_LABEL_HEIGHT = 18
PREVIEW_FRAME_WIDTH = WIDTH * PREVIEW_SCALE
PREVIEW_FRAME_HEIGHT = HEIGHT * PREVIEW_SCALE
PREVIEW_WIDTH = (
    PREVIEW_MARGIN * 2
    + PREVIEW_COLUMNS * PREVIEW_FRAME_WIDTH
    + (PREVIEW_COLUMNS - 1) * PREVIEW_GAP
)
PREVIEW_HEIGHT = (
    PREVIEW_MARGIN * 2
    + PREVIEW_ROWS * (PREVIEW_FRAME_HEIGHT + PREVIEW_LABEL_HEIGHT)
    + (PREVIEW_ROWS - 1) * PREVIEW_GAP
)

PAYLOAD_FILENAME = "xc-body-layered.rgb565le"
MANIFEST_FILENAME = "xc-body-layered.manifest.json"
PREVIEW_FILENAME = "xc-body-layered-preview.png"

ToolCaller = Callable[[str, Mapping[str, object]], object]
Color = tuple[int, int, int]

_BACKGROUND = (22, 32, 39)
_COLLAR = (45, 58, 65)
_SHELL_SHADOW = (174, 190, 198)
_SHELL = (205, 217, 223)
_SHELL_LIGHT = (240, 247, 249)
_OUTLINE = (150, 166, 174)
_FACE = (244, 249, 250)
_EYE = (78, 94, 116)
_EYE_HIGHLIGHT = (255, 255, 255)
_MOUTH = (101, 117, 126)
_GREEN = (79, 226, 91)
_GREEN_SOFT = (116, 239, 126)
_CHEEK = (255, 151, 171)
_CHEEK_SOFT = (255, 190, 203)
_TEAR = (91, 205, 232)


class AvatarAssetValidationError(ValueError):
    """A payload or manifest does not match the layered runtime contract."""


def rgb565_le(red: int, green: int, blue: int) -> bytes:
    """Encode one 8-bit RGB color as an RGB565 little-endian pixel."""

    for channel in (red, green, blue):
        if isinstance(channel, bool) or not isinstance(channel, int):
            raise ValueError("RGB channels must be integers")
        if not 0 <= channel <= 255:
            raise ValueError("RGB channels must be within 0..255")
    value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
    return struct.pack("<H", value)


def count_different_pixels(first: bytes, second: bytes) -> int:
    """Count unequal RGB565 pixels in two equally sized frames."""

    if len(first) != len(second) or len(first) % BYTES_PER_PIXEL:
        raise ValueError("frames must have equal whole-pixel lengths")
    return sum(
        first[offset : offset + 2] != second[offset : offset + 2]
        for offset in range(0, len(first), 2)
    )


class _Canvas:
    """Render in RGB888 above device resolution, then area-downsample."""

    def __init__(self, color: Color):
        self.width = DESIGN_WIDTH * DRAW_SCALE
        self.height = DESIGN_HEIGHT * DRAW_SCALE
        self.pixels = [_rgb_value(color)] * (self.width * self.height)

    def _raw_pixel(self, x: int, y: int, color: Color) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.pixels[y * self.width + x] = _rgb_value(color)

    def _raw_rectangle(
        self, x0: int, y0: int, x1: int, y1: int, color: Color
    ) -> None:
        value = _rgb_value(color)
        left = max(0, min(x0, x1))
        right = min(self.width - 1, max(x0, x1))
        top = max(0, min(y0, y1))
        bottom = min(self.height - 1, max(y0, y1))
        for y in range(top, bottom + 1):
            start = y * self.width + left
            self.pixels[start : start + right - left + 1] = [value] * (
                right - left + 1
            )

    def pixel(self, x: int, y: int, color: Color) -> None:
        self._raw_rectangle(
            x * DRAW_SCALE,
            y * DRAW_SCALE,
            (x + 1) * DRAW_SCALE - 1,
            (y + 1) * DRAW_SCALE - 1,
            color,
        )

    def rectangle(
        self, x0: int, y0: int, x1: int, y1: int, color: Color
    ) -> None:
        self._raw_rectangle(
            x0 * DRAW_SCALE,
            y0 * DRAW_SCALE,
            (x1 + 1) * DRAW_SCALE - 1,
            (y1 + 1) * DRAW_SCALE - 1,
            color,
        )

    def ellipse(
        self, cx: int, cy: int, rx: int, ry: int, color: Color
    ) -> None:
        if rx <= 0 or ry <= 0:
            return
        center_x = (cx + 0.5) * DRAW_SCALE
        center_y = (cy + 0.5) * DRAW_SCALE
        scaled_rx = (rx + 0.5) * DRAW_SCALE
        scaled_ry = (ry + 0.5) * DRAW_SCALE
        rx_squared = scaled_rx * scaled_rx
        ry_squared = scaled_ry * scaled_ry
        limit = rx_squared * ry_squared
        for y in range((cy - ry) * DRAW_SCALE, (cy + ry + 1) * DRAW_SCALE):
            dy_squared = (y + 0.5 - center_y) ** 2
            for x in range(
                (cx - rx) * DRAW_SCALE,
                (cx + rx + 1) * DRAW_SCALE,
            ):
                dx_squared = (x + 0.5 - center_x) ** 2
                if dx_squared * ry_squared + dy_squared * rx_squared <= limit:
                    self._raw_pixel(x, y, color)

    def polygon(self, points: Sequence[tuple[int, int]], color: Color) -> None:
        if len(points) < 3:
            return
        scaled = tuple(
            ((x + 0.5) * DRAW_SCALE, (y + 0.5) * DRAW_SCALE)
            for x, y in points
        )
        minimum_y = max(0, int(min(point[1] for point in scaled)))
        maximum_y = min(
            self.height - 1, int(max(point[1] for point in scaled))
        )
        for y in range(minimum_y, maximum_y + 1):
            scan_y = y + 0.5
            intersections: list[float] = []
            previous = scaled[-1]
            for current in scaled:
                x1, y1 = previous
                x2, y2 = current
                if y1 != y2 and min(y1, y2) <= scan_y < max(y1, y2):
                    numerator = (scan_y - y1) * (x2 - x1)
                    intersections.append(x1 + numerator / (y2 - y1))
                previous = current
            intersections.sort()
            for index in range(0, len(intersections) - 1, 2):
                left = int(intersections[index] + 0.999999)
                right = int(intersections[index + 1])
                self._raw_rectangle(left, y, right, y, color)

    def line(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: Color,
        width: int = 1,
    ) -> None:
        start_x = (x0 + 0.5) * DRAW_SCALE
        start_y = (y0 + 0.5) * DRAW_SCALE
        end_x = (x1 + 0.5) * DRAW_SCALE
        end_y = (y1 + 0.5) * DRAW_SCALE
        delta_x = end_x - start_x
        delta_y = end_y - start_y
        length_squared = delta_x * delta_x + delta_y * delta_y
        radius = width * DRAW_SCALE / 2
        left = max(0, int(min(start_x, end_x) - radius))
        right = min(self.width - 1, int(max(start_x, end_x) + radius))
        top = max(0, int(min(start_y, end_y) - radius))
        bottom = min(self.height - 1, int(max(start_y, end_y) + radius))
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                if length_squared:
                    projection = (
                        (x + 0.5 - start_x) * delta_x
                        + (y + 0.5 - start_y) * delta_y
                    ) / length_squared
                    projection = max(0.0, min(1.0, projection))
                else:
                    projection = 0.0
                nearest_x = start_x + projection * delta_x
                nearest_y = start_y + projection * delta_y
                distance_squared = (
                    (x + 0.5 - nearest_x) ** 2
                    + (y + 0.5 - nearest_y) ** 2
                )
                if distance_squared <= radius * radius:
                    self._raw_pixel(x, y, color)

    def to_rgb565(self) -> bytes:
        encoded = bytearray(FRAME_BYTES)
        sample_count = SUPERSAMPLE * SUPERSAMPLE
        for target_y in range(HEIGHT):
            for target_x in range(WIDTH):
                red = green = blue = 0
                source_x = target_x * SUPERSAMPLE
                source_y = target_y * SUPERSAMPLE
                for offset_y in range(SUPERSAMPLE):
                    start = (source_y + offset_y) * self.width + source_x
                    for value in self.pixels[start : start + SUPERSAMPLE]:
                        red += value >> 16
                        green += (value >> 8) & 0xFF
                        blue += value & 0xFF
                color = rgb565_le(
                    (red + sample_count // 2) // sample_count,
                    (green + sample_count // 2) // sample_count,
                    (blue + sample_count // 2) // sample_count,
                )
                index = target_y * WIDTH + target_x
                encoded[index * 2 : index * 2 + 2] = color
        return bytes(encoded)


def _rgb_value(color: Color) -> int:
    red, green, blue = color
    return red << 16 | green << 8 | blue

def _draw_base(canvas: _Canvas) -> None:
    canvas.polygon(
        [(45, 119), (49, 97), (111, 97), (115, 119)],
        _COLLAR,
    )

    for center in (20, 140):
        canvas.ellipse(center, 61, 15, 22, _SHELL_LIGHT)
        canvas.ellipse(center, 61, 11, 17, _SHELL_SHADOW)
    canvas.line(20, 53, 20, 69, _GREEN_SOFT, 4)
    canvas.line(140, 53, 140, 69, _GREEN_SOFT, 4)

    canvas.ellipse(80, 11, 13, 11, _SHELL_LIGHT)
    canvas.ellipse(80, 11, 9, 8, _SHELL_SHADOW)

    canvas.ellipse(80, 59, 58, 47, _SHELL_LIGHT)
    canvas.ellipse(80, 59, 54, 43, _SHELL)
    canvas.ellipse(80, 64, 41, 34, _OUTLINE)
    canvas.ellipse(80, 63, 38, 31, _FACE)

    canvas.polygon(
        [(28, 30), (44, 33), (42, 69), (36, 83), (27, 94), (22, 70)],
        _OUTLINE,
    )
    canvas.polygon(
        [(30, 34), (40, 36), (39, 68), (34, 80), (29, 86), (26, 69)],
        _SHELL_LIGHT,
    )
    canvas.polygon(
        [(132, 30), (116, 33), (118, 69), (124, 83), (133, 94), (138, 70)],
        _OUTLINE,
    )
    canvas.polygon(
        [(130, 34), (120, 36), (121, 68), (126, 80), (131, 86), (134, 69)],
        _SHELL_LIGHT,
    )

    canvas.line(49, 103, 80, 117, _GREEN, 3)
    canvas.line(80, 117, 111, 103, _GREEN, 3)


def _draw_rune(canvas: _Canvas, expression: str) -> None:
    del expression
    canvas.polygon(
        [
            (79, 22),
            (86, 22),
            (82, 28),
            (87, 28),
            (76, 39),
            (79, 31),
            (74, 31),
        ],
        _GREEN,
    )


def _open_eye(canvas: _Canvas, cx: int, *, gaze: int = 0) -> None:
    canvas.ellipse(cx, 59, 9, 12, _EYE)
    canvas.ellipse(cx - 3 + gaze, 55, 2, 3, _EYE_HIGHLIGHT)


def _draw_eyes(canvas: _Canvas, state: str) -> None:
    if state == "open":
        _open_eye(canvas, 60)
        _open_eye(canvas, 100)
    elif state == "half":
        for center in (60, 100):
            canvas.ellipse(center, 61, 10, 6, _EYE)
            canvas.ellipse(center - 3, 59, 2, 2, _EYE_HIGHLIGHT)
    elif state == "closed":
        for center in (60, 100):
            canvas.line(center - 9, 58, center, 62, _EYE, 3)
            canvas.line(center, 62, center + 9, 58, _EYE, 3)
    elif state == "happy":
        for center in (60, 100):
            canvas.line(center - 9, 60, center, 53, _EYE, 3)
            canvas.line(center, 53, center + 9, 60, _EYE, 3)
    elif state == "thinking":
        _open_eye(canvas, 58, gaze=-1)
        _open_eye(canvas, 98, gaze=-1)
        canvas.rectangle(49, 47, 67, 49, _FACE)
    elif state == "sad":
        canvas.line(51, 57, 59, 53, _EYE, 3)
        canvas.line(59, 53, 68, 58, _EYE, 3)
        canvas.line(92, 58, 101, 53, _EYE, 3)
        canvas.line(101, 53, 109, 57, _EYE, 3)
    elif state == "surprised":
        for center in (60, 100):
            canvas.ellipse(center, 58, 10, 14, _EYE)
            canvas.ellipse(center - 3, 53, 2, 3, _EYE_HIGHLIGHT)
    elif state == "embarrassed":
        for center in (60, 100):
            canvas.line(center - 9, 62, center, 56, _EYE, 3)
            canvas.line(center, 56, center + 9, 62, _EYE, 3)
    else:
        raise ValueError(f"unknown eye state: {state}")


def _draw_mouth(canvas: _Canvas, state: str) -> None:
    if state == "closed":
        canvas.line(70, 81, 80, 84, _MOUTH, 3)
        canvas.line(80, 84, 90, 81, _MOUTH, 3)
    elif state == "half":
        canvas.line(69, 80, 80, 84, _MOUTH, 3)
        canvas.line(80, 84, 91, 80, _MOUTH, 3)
        canvas.line(75, 87, 85, 87, _MOUTH, 2)
    elif state == "open":
        canvas.line(69, 80, 80, 83, _MOUTH, 3)
        canvas.line(80, 83, 91, 80, _MOUTH, 3)
        canvas.line(73, 86, 80, 88, _MOUTH, 2)
        canvas.line(80, 88, 87, 86, _MOUTH, 2)
    elif state == "e":
        canvas.line(65, 82, 80, 84, _MOUTH, 3)
        canvas.line(80, 84, 95, 82, _MOUTH, 3)
        canvas.line(69, 87, 91, 87, _MOUTH, 2)
    elif state == "u":
        canvas.line(74, 79, 74, 85, _MOUTH, 3)
        canvas.line(74, 85, 80, 89, _MOUTH, 3)
        canvas.line(80, 89, 86, 85, _MOUTH, 3)
        canvas.line(86, 85, 86, 79, _MOUTH, 3)
    elif state == "happy":
        canvas.line(68, 79, 74, 84, _MOUTH, 3)
        canvas.line(74, 84, 80, 87, _MOUTH, 3)
        canvas.line(80, 87, 86, 84, _MOUTH, 3)
        canvas.line(86, 84, 92, 79, _MOUTH, 3)
    elif state == "thinking":
        canvas.line(71, 85, 80, 81, _MOUTH, 3)
        canvas.line(80, 81, 89, 85, _MOUTH, 3)
    elif state == "sad":
        canvas.line(69, 87, 75, 82, _MOUTH, 3)
        canvas.line(75, 82, 80, 80, _MOUTH, 3)
        canvas.line(80, 80, 85, 82, _MOUTH, 3)
        canvas.line(85, 82, 91, 87, _MOUTH, 3)
    elif state == "surprised":
        canvas.line(75, 82, 85, 82, _MOUTH, 3)
        canvas.line(77, 87, 83, 87, _MOUTH, 2)
    elif state == "embarrassed":
        canvas.line(71, 81, 80, 85, _MOUTH, 3)
        canvas.line(80, 85, 89, 81, _MOUTH, 3)
    else:
        raise ValueError(f"unknown mouth state: {state}")


def _draw_expression_details(canvas: _Canvas, expression: str) -> None:
    if expression == "sad":
        canvas.polygon([(112, 67), (108, 74), (112, 78), (116, 74)], _TEAR)
        canvas.ellipse(112, 74, 3, 4, _TEAR)

    canvas.ellipse(47, 75, 6, 4, _CHEEK_SOFT)
    canvas.ellipse(113, 75, 6, 4, _CHEEK_SOFT)

    if expression == "happy":
        canvas.ellipse(47, 75, 7, 4, _CHEEK)
        canvas.ellipse(113, 75, 7, 4, _CHEEK)
    elif expression == "thinking":
        canvas.ellipse(113, 75, 6, 4, _CHEEK)
    elif expression == "surprised":
        canvas.ellipse(47, 75, 4, 3, _CHEEK)
        canvas.ellipse(113, 75, 4, 3, _CHEEK)
    elif expression == "embarrassed":
        canvas.ellipse(47, 75, 8, 5, _CHEEK)
        canvas.ellipse(113, 75, 8, 5, _CHEEK)


def _render_frame(frame_name: str) -> bytes:
    layer, state = frame_name.split("_", 1)
    expression = "idle"
    eye_state = "open"
    mouth_state = "closed"
    if layer == "face":
        expression = state
        eye_state = state if state != "idle" else "open"
        mouth_state = state if state != "idle" else "closed"
        if state == "surprised":
            mouth_state = "open"
    elif layer == "eye":
        eye_state = state
    elif layer == "mouth":
        mouth_state = state
    else:
        raise ValueError(f"unknown frame layer: {layer}")

    canvas = _Canvas(_BACKGROUND)
    _draw_base(canvas)
    _draw_rune(canvas, expression)
    _draw_eyes(canvas, eye_state)
    _draw_mouth(canvas, mouth_state)
    _draw_expression_details(canvas, expression)
    return canvas.to_rgb565()


def generate_avatar_set() -> tuple[bytes, dict[str, object], bytes]:
    """Generate the raw payload, manifest, and PNG contact sheet in memory."""

    frames = tuple(_render_frame(name) for name in FRAME_ORDER)
    payload = b"".join(frames)
    manifest_frames = []
    for index, (name, frame) in enumerate(zip(FRAME_ORDER, frames)):
        layer, state = name.split("_", 1)
        manifest_frames.append(
            {
                "index": index,
                "id": name,
                "layer": layer,
                "name": state,
                "offset": index * FRAME_BYTES,
                "size": FRAME_BYTES,
                "sha256": sha256(frame).hexdigest(),
            }
        )
    manifest: dict[str, object] = {
        "version": MANIFEST_VERSION,
        "mode": MODE,
        "dimensions": {"width": WIDTH, "height": HEIGHT},
        "pixel_format": PIXEL_FORMAT,
        "frame_bytes": FRAME_BYTES,
        "frame_order": list(FRAME_ORDER),
        "payload_size": len(payload),
        "payload_sha256": sha256(payload).hexdigest(),
        "frames": manifest_frames,
    }
    validate_avatar_set(payload, manifest)
    return payload, manifest, _contact_sheet(frames)


def manifest_json(manifest: Mapping[str, object]) -> str:
    """Serialize a manifest in the stable on-disk representation."""

    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def validate_avatar_set(
    payload: bytes, manifest: Mapping[str, object]
) -> None:
    """Fail closed unless payload and manifest match the exact runtime format."""

    if not isinstance(payload, bytes):
        raise AvatarAssetValidationError("payload must be bytes")
    expected_fields = {
        "version": MANIFEST_VERSION,
        "mode": MODE,
        "dimensions": {"width": WIDTH, "height": HEIGHT},
        "pixel_format": PIXEL_FORMAT,
        "frame_bytes": FRAME_BYTES,
        "frame_order": list(FRAME_ORDER),
        "payload_size": TOTAL_BYTES,
    }
    for field, expected in expected_fields.items():
        if manifest.get(field) != expected:
            raise AvatarAssetValidationError(
                f"manifest {field!r} does not match the layered contract"
            )
    if len(payload) != TOTAL_BYTES:
        raise AvatarAssetValidationError(
            f"payload size must be exactly {TOTAL_BYTES} bytes"
        )
    if manifest.get("payload_sha256") != sha256(payload).hexdigest():
        raise AvatarAssetValidationError("payload SHA-256 does not match")

    frame_entries = manifest.get("frames")
    if not isinstance(frame_entries, list) or len(frame_entries) != len(
        FRAME_ORDER
    ):
        raise AvatarAssetValidationError("manifest must describe all 14 frames")
    frames: list[bytes] = []
    for index, (name, entry) in enumerate(zip(FRAME_ORDER, frame_entries)):
        if not isinstance(entry, Mapping):
            raise AvatarAssetValidationError("frame manifest entry must be an object")
        layer, state = name.split("_", 1)
        expected_entry = {
            "index": index,
            "id": name,
            "layer": layer,
            "name": state,
            "offset": index * FRAME_BYTES,
            "size": FRAME_BYTES,
        }
        for field, expected in expected_entry.items():
            if entry.get(field) != expected:
                raise AvatarAssetValidationError(
                    f"frame {index} {field!r} does not match expected order"
                )
        start = index * FRAME_BYTES
        frame = payload[start : start + FRAME_BYTES]
        if entry.get("sha256") != sha256(frame).hexdigest():
            raise AvatarAssetValidationError(
                f"frame {index} SHA-256 does not match"
            )
        if _artwork_pixel_count(frame) < MIN_ARTWORK_PIXELS:
            raise AvatarAssetValidationError(
                f"frame {index} does not contain a full nonblank face"
            )
        frames.append(frame)

    for first_index in range(FACE_FRAME_COUNT):
        for second_index in range(first_index + 1, FACE_FRAME_COUNT):
            difference = count_different_pixels(
                frames[first_index], frames[second_index]
            )
            if difference < MIN_FACE_DIFFERENCE_PIXELS:
                raise AvatarAssetValidationError(
                    "face frames are not meaningfully distinct: "
                    f"{FRAME_ORDER[first_index]} and "
                    f"{FRAME_ORDER[second_index]} differ by {difference} pixels"
                )


def load_validated_avatar_set(
    call_tool: ToolCaller,
    *,
    payload_path: str | Path,
    manifest_path: str | Path,
    archive_path: str,
) -> object:
    """Validate local bytes, then request upstream runtime layered loading."""

    if not isinstance(archive_path, str) or not archive_path.strip():
        raise AvatarAssetValidationError("archive_path must be a non-empty string")
    payload = Path(payload_path).read_bytes()
    try:
        decoded = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AvatarAssetValidationError("manifest is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, Mapping):
        raise AvatarAssetValidationError("manifest JSON must be an object")
    validate_avatar_set(payload, decoded)
    return call_tool(
        "load_avatar_set",
        {"archive_path": archive_path, "mode": MODE},
    )


def write_avatar_assets(output_directory: str | Path) -> dict[str, Path]:
    """Build validated artifacts beneath one caller-selected directory."""

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    payload, manifest, preview = generate_avatar_set()
    paths = {
        "payload": output / PAYLOAD_FILENAME,
        "manifest": output / MANIFEST_FILENAME,
        "preview": output / PREVIEW_FILENAME,
    }
    paths["payload"].write_bytes(payload)
    paths["manifest"].write_text(manifest_json(manifest), encoding="utf-8")
    paths["preview"].write_bytes(preview)
    return paths


def _artwork_pixel_count(frame: bytes) -> int:
    background = frame[:2]
    return sum(
        frame[offset : offset + 2] != background
        for offset in range(0, len(frame), 2)
    )


def _rgb_from_value(value: int) -> Color:
    red = (value >> 11) & 0x1F
    green = (value >> 5) & 0x3F
    blue = value & 0x1F
    return (
        (red * 255 + 15) // 31,
        (green * 255 + 31) // 63,
        (blue * 255 + 15) // 31,
    )


_FONT = {
    " ": ("00000",) * 7,
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
}


def _contact_sheet(frames: Sequence[bytes]) -> bytes:
    label_background = bytes((8, 19, 29))
    pixels = bytearray(label_background * (PREVIEW_WIDTH * PREVIEW_HEIGHT))
    for index, (name, frame) in enumerate(zip(FRAME_ORDER, frames)):
        column = index % PREVIEW_COLUMNS
        row = index // PREVIEW_COLUMNS
        left = PREVIEW_MARGIN + column * (PREVIEW_FRAME_WIDTH + PREVIEW_GAP)
        top = PREVIEW_MARGIN + row * (
            PREVIEW_FRAME_HEIGHT + PREVIEW_LABEL_HEIGHT + PREVIEW_GAP
        )
        _paste_frame(pixels, frame, left, top)
        label = f"{index + 1:02} {name.replace('_', ' ').upper()}"
        _draw_label(pixels, label, left + 4, top + PREVIEW_FRAME_HEIGHT + 2)
    return _encode_png(PREVIEW_WIDTH, PREVIEW_HEIGHT, pixels)


def _paste_frame(
    preview: bytearray, frame: bytes, left: int, top: int
) -> None:
    values = struct.iter_unpack("<H", frame)
    for source_index, (value,) in enumerate(values):
        source_x = source_index % WIDTH
        source_y = source_index // WIDTH
        color = _rgb_from_value(value)
        for offset_y in range(PREVIEW_SCALE):
            y = top + source_y * PREVIEW_SCALE + offset_y
            for offset_x in range(PREVIEW_SCALE):
                x = left + source_x * PREVIEW_SCALE + offset_x
                _set_preview_pixel(preview, x, y, color)


def _draw_label(
    preview: bytearray, label: str, left: int, top: int
) -> None:
    cursor = left
    for character in label:
        glyph = _FONT[character]
        for glyph_y, row in enumerate(glyph):
            for glyph_x, cell in enumerate(row):
                if cell == "0":
                    continue
                for offset_y in range(2):
                    for offset_x in range(2):
                        _set_preview_pixel(
                            preview,
                            cursor + glyph_x * 2 + offset_x,
                            top + glyph_y * 2 + offset_y,
                            _GREEN,
                        )
        cursor += 12


def _set_preview_pixel(
    preview: bytearray, x: int, y: int, color: Color
) -> None:
    if not 0 <= x < PREVIEW_WIDTH or not 0 <= y < PREVIEW_HEIGHT:
        return
    offset = (y * PREVIEW_WIDTH + x) * 3
    preview[offset : offset + 3] = bytes(color)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(data, checksum) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", checksum)
    )


def _encode_png(width: int, height: int, pixels: bytes) -> bytes:
    stride = width * 3
    scanlines = b"".join(
        b"\x00" + pixels[row * stride : (row + 1) * stride]
        for row in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(scanlines, level=9))
        + _png_chunk(b"IEND", b"")
    )
