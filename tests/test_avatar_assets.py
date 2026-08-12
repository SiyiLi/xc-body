import copy
from hashlib import sha256
import json
from pathlib import Path
import struct
import tempfile
import unittest

from stackchan.avatar_assets import (
    FRAME_BYTES,
    FRAME_ORDER,
    HEIGHT,
    MANIFEST_FILENAME,
    MIN_ARTWORK_PIXELS,
    MIN_FACE_DIFFERENCE_PIXELS,
    OUTPUT_SCALE,
    PAYLOAD_FILENAME,
    PREVIEW_FILENAME,
    PREVIEW_HEIGHT,
    PREVIEW_WIDTH,
    TOTAL_BYTES,
    WIDTH,
    AvatarAssetValidationError,
    count_different_pixels,
    generate_avatar_set,
    load_validated_avatar_set,
    manifest_json,
    rgb565_le,
    validate_avatar_set,
    write_avatar_assets,
)
from stackchan.calibration import measured_k151_cores3_calibration


ROOT = Path(__file__).resolve().parents[1]


def frame_pixel(frame, x, y):
    x = x * OUTPUT_SCALE + OUTPUT_SCALE // 2
    y = y * OUTPUT_SCALE + OUTPUT_SCALE // 2
    offset = (y * WIDTH + x) * 2
    return frame[offset : offset + 2]


class AvatarAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload, cls.manifest, cls.preview = generate_avatar_set()
        cls.frames = [
            cls.payload[index * FRAME_BYTES : (index + 1) * FRAME_BYTES]
            for index in range(len(FRAME_ORDER))
        ]

    def test_payload_has_exact_upstream_size_order_and_offsets(self):
        self.assertEqual(len(self.payload), 2_150_400)
        self.assertEqual(len(self.payload), TOTAL_BYTES)
        self.assertEqual(
            FRAME_ORDER,
            (
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
            ),
        )
        self.assertEqual(self.manifest["frame_order"], list(FRAME_ORDER))
        for index, entry in enumerate(self.manifest["frames"]):
            self.assertEqual(entry["index"], index)
            self.assertEqual(entry["id"], FRAME_ORDER[index])
            self.assertEqual(entry["offset"], index * 153_600)
            self.assertEqual(entry["size"], 153_600)

    def test_rgb565_encoding_is_little_endian(self):
        known_colors = {
            (0, 0, 0): b"\x00\x00",
            (255, 255, 255): b"\xff\xff",
            (255, 0, 0): b"\x00\xf8",
            (0, 255, 0): b"\xe0\x07",
            (0, 0, 255): b"\x1f\x00",
            (0, 255, 255): b"\xff\x07",
        }
        for color, expected in known_colors.items():
            with self.subTest(color=color):
                self.assertEqual(rgb565_le(*color), expected)

    def test_payload_manifest_and_preview_are_deterministic(self):
        payload, manifest, preview = generate_avatar_set()

        self.assertEqual(payload, self.payload)
        self.assertEqual(manifest_json(manifest), manifest_json(self.manifest))
        self.assertEqual(preview, self.preview)

    def test_six_faces_are_meaningfully_distinct(self):
        for first_index in range(6):
            for second_index in range(first_index + 1, 6):
                with self.subTest(
                    first=FRAME_ORDER[first_index],
                    second=FRAME_ORDER[second_index],
                ):
                    difference = count_different_pixels(
                        self.frames[first_index],
                        self.frames[second_index],
                    )
                    self.assertGreaterEqual(
                        difference, MIN_FACE_DIFFERENCE_PIXELS
                    )

    def test_idle_frame_has_soft_bot_identity_anchors(self):
        idle = self.frames[0]
        anchors = {
            "background": ((0, 0), (22, 32, 39)),
            "silver topknot": ((80, 8), (174, 190, 198)),
            "rounded silver head": ((68, 22), (205, 217, 223)),
            "left side lock": ((30, 55), (240, 247, 249)),
            "right side lock": ((130, 55), (240, 247, 249)),
            "green forehead rune": ((80, 25), (79, 226, 91)),
            "soft face panel": ((80, 44), (244, 249, 250)),
            "left gentle eye": ((60, 59), (78, 94, 116)),
            "right gentle eye": ((100, 59), (78, 94, 116)),
            "eye highlight": ((57, 55), (255, 255, 255)),
            "left cheek light": ((47, 75), (255, 190, 203)),
            "right cheek light": ((113, 75), (255, 190, 203)),
            "dark collar": ((80, 110), (45, 58, 65)),
            "green collar trim": ((80, 117), (79, 226, 91)),
        }
        for name, (point, color) in anchors.items():
            with self.subTest(anchor=name):
                self.assertEqual(
                    frame_pixel(idle, *point),
                    rgb565_le(*color),
                )

    def test_every_frame_preserves_bilateral_inner_ear_indicators(self):
        indicator = rgb565_le(116, 239, 126)
        for name, frame in zip(FRAME_ORDER, self.frames):
            with self.subTest(frame=name, side="left"):
                self.assertTrue(
                    all(
                        frame_pixel(frame, 20, y) == indicator
                        for y in range(54, 69)
                    )
                )
            with self.subTest(frame=name, side="right"):
                self.assertTrue(
                    all(
                        frame_pixel(frame, 140, y) == indicator
                        for y in range(54, 69)
                    )
                )

    def test_every_frame_preserves_bilateral_baseline_cheek_lights(self):
        cheek_colors = {
            rgb565_le(255, 151, 171),
            rgb565_le(255, 190, 203),
        }
        for name, frame in zip(FRAME_ORDER, self.frames):
            for side, center in (("left", 47), ("right", 113)):
                with self.subTest(frame=name, side=side):
                    self.assertTrue(
                        all(
                            frame_pixel(frame, x, 75) in cheek_colors
                            for x in range(center - 3, center + 4)
                        )
                    )

    def test_every_frame_leaves_a_clear_right_boundary_after_the_ear(self):
        background = rgb565_le(22, 32, 39)
        for name, frame in zip(FRAME_ORDER, self.frames):
            with self.subTest(frame=name):
                self.assertTrue(
                    all(
                        frame_pixel(frame, x, y) == background
                        for y in range(120)
                        for x in range(158, 160)
                    )
                )

    def test_every_frame_uses_antialiased_line_mouths_without_cavities(self):
        face_panel = rgb565_le(244, 249, 250)
        mouth_line = rgb565_le(101, 117, 126)
        for name, frame in zip(FRAME_ORDER, self.frames):
            mouth_pixels = [
                frame[(y * WIDTH + x) * 2 : (y * WIDTH + x) * 2 + 2]
                for y in range(77 * OUTPUT_SCALE, 91 * OUTPUT_SCALE)
                for x in range(64 * OUTPUT_SCALE, 97 * OUTPUT_SCALE)
            ]
            blended = set(mouth_pixels) - {face_panel, mouth_line}
            with self.subTest(frame=name):
                self.assertIn(mouth_line, mouth_pixels)
                self.assertTrue(blended)
                self.assertLessEqual(mouth_pixels.count(mouth_line), 800)
                self.assertGreater(mouth_pixels.count(face_panel), 960)

    def test_eye_and_mouth_frames_are_complete_nonblank_faces(self):
        for name, frame in zip(FRAME_ORDER[6:], self.frames[6:]):
            pixels = [pixel[0] for pixel in struct.iter_unpack("<H", frame)]
            background = pixels[0]
            artwork_pixels = sum(pixel != background for pixel in pixels)
            with self.subTest(frame=name):
                self.assertGreaterEqual(artwork_pixels, MIN_ARTWORK_PIXELS)
                self.assertGreater(len(set(pixels)), 5)

    def test_manifest_hashes_validate_and_tampering_fails(self):
        validate_avatar_set(self.payload, self.manifest)
        self.assertEqual(
            self.manifest["payload_sha256"], sha256(self.payload).hexdigest()
        )
        for entry, frame in zip(self.manifest["frames"], self.frames):
            self.assertEqual(entry["sha256"], sha256(frame).hexdigest())

        wrong_size = self.payload[:-2]
        wrong_order = copy.deepcopy(self.manifest)
        wrong_order["frame_order"][0:2] = reversed(
            wrong_order["frame_order"][0:2]
        )
        wrong_hash = copy.deepcopy(self.manifest)
        wrong_hash["frames"][0]["sha256"] = "0" * 64
        tampered = bytes([self.payload[0] ^ 1]) + self.payload[1:]
        invalid_cases = (
            (wrong_size, self.manifest),
            (self.payload, wrong_order),
            (self.payload, wrong_hash),
            (tampered, self.manifest),
        )
        for payload, manifest in invalid_cases:
            with self.subTest(size=len(payload), order=manifest["frame_order"][:2]):
                with self.assertRaises(AvatarAssetValidationError):
                    validate_avatar_set(payload, manifest)

    def test_validator_rejects_hash_consistent_cosmetic_face_duplicate(self):
        duplicated = self.frames[0] + self.frames[0] + b"".join(self.frames[2:])
        manifest = copy.deepcopy(self.manifest)
        manifest["payload_sha256"] = sha256(duplicated).hexdigest()
        manifest["frames"][1]["sha256"] = sha256(self.frames[0]).hexdigest()

        with self.assertRaisesRegex(
            AvatarAssetValidationError, "not meaningfully distinct"
        ):
            validate_avatar_set(duplicated, manifest)

    def test_png_has_expected_signature_dimensions_and_sheet_layout(self):
        self.assertEqual(self.preview[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.preview[12:16], b"IHDR")
        width, height = struct.unpack(">II", self.preview[16:24])
        self.assertEqual((width, height), (PREVIEW_WIDTH, PREVIEW_HEIGHT))
        self.assertEqual(PREVIEW_WIDTH, 1_308)
        self.assertEqual(PREVIEW_HEIGHT, 1_060)

    def test_loader_validates_before_calling_upstream_tool(self):
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            paths = write_avatar_assets(temporary)
            calls = []

            def call_tool(name, arguments):
                calls.append((name, arguments))
                return {"ok": True}

            result = load_validated_avatar_set(
                call_tool,
                payload_path=paths["payload"],
                manifest_path=paths["manifest"],
                archive_path="caller-supplied/avatar.bin",
            )
            self.assertEqual(result, {"ok": True})
            self.assertEqual(
                calls,
                [
                    (
                        "load_avatar_set",
                        {
                            "archive_path": "caller-supplied/avatar.bin",
                            "mode": "layered-320x240",
                        },
                    )
                ],
            )

            paths["payload"].write_bytes(self.payload[:-2])
            calls.clear()
            with self.assertRaises(AvatarAssetValidationError):
                load_validated_avatar_set(
                    call_tool,
                    payload_path=paths["payload"],
                    manifest_path=paths["manifest"],
                    archive_path="caller-supplied/avatar.bin",
                )
            self.assertEqual(calls, [])

    def test_builder_uses_expected_artifact_names_and_valid_json(self):
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            paths = write_avatar_assets(temporary)

            self.assertEqual(paths["payload"].name, PAYLOAD_FILENAME)
            self.assertEqual(paths["manifest"].name, MANIFEST_FILENAME)
            self.assertEqual(paths["preview"].name, PREVIEW_FILENAME)
            decoded = json.loads(paths["manifest"].read_text())
            validate_avatar_set(paths["payload"].read_bytes(), decoded)

    def test_production_verified_faces_remains_empty(self):
        calibration = measured_k151_cores3_calibration()

        self.assertEqual(calibration.verified_faces, frozenset())


if __name__ == "__main__":
    unittest.main()
