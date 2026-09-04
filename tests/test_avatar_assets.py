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
    MANIFEST_FILENAME,
    MIN_ARTWORK_PIXELS,
    MIN_FACE_DIFFERENCE_PIXELS,
    PAYLOAD_FILENAME,
    PREVIEW_FILENAME,
    PREVIEW_HEIGHT,
    PREVIEW_WIDTH,
    SEMANTIC_FACE_BOUNDS,
    TOTAL_BYTES,
    WIDTH,
    AvatarAssetValidationError,
    count_different_pixels,
    generate_avatar_set,
    manifest_json,
    rgb565_le,
    validate_avatar_set,
    write_avatar_assets,
)


ROOT = Path(__file__).resolve().parents[1]
EXPRESSION_ASSETS = (
    "idle",
    "agree",
    "pleased",
    "curious",
    "concerned",
    "surprised",
    "embarrassed",
    "mischievous",
)


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
        self.assertEqual(
            (
                ROOT
                / "firmware/main/boards/stackchan/assets"
                / PAYLOAD_FILENAME
            ).read_bytes(),
            payload,
        )
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

    def test_eye_and_mouth_frames_are_complete_semantic_faces(self):
        for name, frame in zip(FRAME_ORDER[6:], self.frames[6:]):
            pixels = [pixel[0] for pixel in struct.iter_unpack("<H", frame)]
            background = pixels[0]
            artwork_pixels = sum(pixel != background for pixel in pixels)
            with self.subTest(frame=name):
                self.assertGreaterEqual(artwork_pixels, MIN_ARTWORK_PIXELS)

    def test_semantic_faces_contain_no_portrait_artwork(self):
        left, top, right, bottom = SEMANTIC_FACE_BOUNDS
        left *= 2
        top *= 2
        right = (right + 1) * 2 - 1
        bottom = (bottom + 1) * 2 - 1

        for name, frame in zip(FRAME_ORDER, self.frames):
            pixels = [pixel[0] for pixel in struct.iter_unpack("<H", frame)]
            background = pixels[0]
            outside = [
                index
                for index, pixel in enumerate(pixels)
                if pixel != background
                and not (
                    left <= index % WIDTH <= right
                    and top <= index // WIDTH <= bottom
                )
            ]
            with self.subTest(frame=name):
                self.assertEqual(outside, [])

    def test_expression_gifs_are_packaged_at_native_screen_size(self):
        assets = ROOT / "firmware/main/boards/stackchan/assets"
        for name in EXPRESSION_ASSETS:
            path = assets / f"expression-{name}.gif"
            encoded = path.read_bytes()
            with self.subTest(expression=name):
                self.assertIn(encoded[:6], (b"GIF87a", b"GIF89a"))
                self.assertEqual(struct.unpack("<HH", encoded[6:10]), (320, 240))

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

if __name__ == "__main__":
    unittest.main()
