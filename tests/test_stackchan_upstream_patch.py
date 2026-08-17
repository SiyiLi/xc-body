import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "stackchan" / "stackchan-mcp-native-avatar.patch"


def added_lines_for(path: str) -> str:
    patch = PATCH_PATH.read_text(encoding="utf-8")
    marker = f"diff --git a/{path} b/{path}\n"
    section = patch.split(marker, 1)[1].split("diff --git ", 1)[0]
    return "\n".join(
        line[1:]
        for line in section.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


class StackChanUpstreamPatchTests(unittest.TestCase):
    def test_patch_adds_authenticated_bounded_opus_endpoint(self):
        added = added_lines_for(
            "gateway/stackchan_mcp/capture_server.py"
        )

        self.assertIn(
            'app.router.add_post("/opus", handle_prepared_opus)',
            added,
        )
        self.assertIn("secrets.compare_digest(", added)
        self.assertIn("PREPARED_OPUS_MAX_TOTAL_BYTES", added)
        self.assertIn("PREPARED_OPUS_MAX_PACKET_BYTES", added)
        self.assertIn("PREPARED_OPUS_MAX_PACKETS", added)
        self.assertIn("PREPARED_OPUS_FRAME_DURATION_MS = 60", added)
        self.assertIn('int.from_bytes(payload[offset : offset + 2], "big")', added)
        self.assertIn("_validate_prepared_opus_packet(packet)", added)
        self.assertIn("_opus_packet_duration_ms(packet)", added)
        self.assertIn("packets must declare mono", added)
        self.assertIn("packets must contain exactly 60 ms", added)
        self.assertIn("trailing bytes after final packet", added)
        self.assertIn("ends inside a packet", added)
        self.assertIn("request.content.iter_chunked(8192)", added)
        self.assertIn("_validate_prepared_opus_message_id(message_id)", added)
        self.assertIn("PREPARED_OPUS_MAX_RECORDED_IDS", added)
        self.assertIn("async with request.app[PREPARED_OPUS_LOCK_KEY]", added)
        self.assertIn("results.get(message_id)", added)

    def test_patch_serializes_and_brackets_paced_device_playback(self):
        added = added_lines_for(
            "gateway/stackchan_mcp/capture_server.py"
        )

        lock = added.index("async with tts_lock:")
        start = added.index('await esp32.send_tts_state("start")')
        frame = added.index("await esp32.send_audio_frame(packet)")
        stop = added.index('await esp32.send_tts_state("stop")')

        self.assertLess(lock, start)
        self.assertLess(start, frame)
        self.assertLess(frame, stop)
        self.assertIn("next_send_time - loop.time()", added)
        self.assertIn("await asyncio.sleep(delay)", added)
        self.assertIn("PREPARED_OPUS_FRAME_DURATION_MS / 1000", added)

    def test_patch_carries_upstream_style_opus_tests(self):
        added = added_lines_for("gateway/tests/test_capture_server.py")

        self.assertIn(
            "test_prepared_opus_parser_rejects_malformed_framing",
            added,
        )
        self.assertIn(
            "test_prepared_opus_parser_rejects_wrong_profile",
            added,
        )
        self.assertIn(
            "5802f9304dbb0de5e392098938ebcae1b1d1dd85",
            added,
        )
        self.assertIn(
            "test_prepared_opus_parser_enforces_total_and_packet_count_limits",
            added,
        )
        self.assertIn(
            "test_prepared_opus_playback_is_locked_bracketed_and_paced",
            added,
        )
        self.assertIn(
            "test_prepared_opus_retry_is_suppressed_while_id_retained",
            added,
        )
        self.assertIn(
            "test_prepared_opus_concurrent_duplicate_is_coalesced",
            added,
        )
        self.assertIn(
            "test_prepared_opus_eviction_allows_replay",
            added,
        )


if __name__ == "__main__":
    unittest.main()
