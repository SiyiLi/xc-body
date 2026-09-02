import asyncio
import base64
import json
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gateway.direct_conversation import (
    DirectPcmBuffer,
    DirectConversationError,
    _PCM_PREBUFFER_BYTES,
    _PCM_PROGRESS_TIMEOUT_SECONDS,
    VoiceMailbox,
    build_direct_turn_report,
    speak_direct_answer,
)
from gateway.pending_thought_runtime import PendingThoughtRuntimeError
from gateway.speech_preparation import (
    EDGE_TTS_CONNECT_TIMEOUT_SECONDS,
    EDGE_TTS_RECEIVE_TIMEOUT_SECONDS,
    stream_speech_pcm,
)


_SHORT_MP3 = base64.b64decode(
    "SUQzBAAAAAAAIlRTU0UAAAAOAAADTGF2ZjYyLjMuMTAwAAAAAAAAAAAAAAD/81jA"
    "AAAAAAAAAAAASW5mbwAAAA8AAAAPAAAE7AAzMzMzMzNBQUFBQUFBUFBQUFBQX19f"
    "X19fX21tbW1tbW18fHx8fHyKioqKioqKmZmZmZmZmaioqKioqLa2tra2trbFxcXF"
    "xcXF1NTU1NTU4uLi4uLi4vHx8fHx8fH///////8AAAAATGF2YzYyLjExAAAAAAAA"
    "AAAAAAAAJANgAAAAAAAABOxTUb10AAAAAAAAAAAAAAD/8yjEAAvAAtY/QRACbjc+"
    "GbbAcAHwfB8Hz5QEAQBCD4PvwQOFwQDDuD7+CEpygf4OO0B/gR3P9H///+gVhFe"
    "C0IHwbQFfIcL/8yjEDA95LpABiqAAtgy9+JtC0EAx2B/geEWIM8YRYgzwAcFDVpA"
    "hmhzv/KJFSKmReL3/6JqYg0Ff4KiI8Cr///8wGJjsUWH/8yjECQ6YwjQBwvAAgqAA"
    "gPMBABkwMggTDuFZMVal85ICxzKgGpMKQCcwCADjA/A0EgFmVlrX5lTvd////TV/"
    "rQ1d++nlkyz/8yjECQ3y1gwAuAsg50MIzPIMetNLf8vHYVx3bf9eP05NQbRt9OXt"
    "jW27a6p+TT9f5tWyfjefUfOeXk0QAEL7c9qbQ1vtojz/8yjEDA8K1g4AuEUsam14"
    "RmeQY9aa+suvyyWJY+sPfeI04rBk1bbThvwr5+2Tja9dP7fjatj/301Hn/VJqn8g"
    "58fe84SOZtr/8yjECg7i1gwADc7IWEZnkGPWmrt+f+uhfH207ZTvlOfpx3h2V5+n"
    "K98x9O2+jfnafo35PVs38pzdS875WTogEAnn3nyzeef/8yjECQ4inhWADcTKDfW+"
    "2EXaMGL02vTq2n6d8Hy9tf0bTTgtBPfTTk74L+22j6dtP7/k1bBdTk0z3pk6AjD/"
    "fvtrfVrLfff/8yjECwvhjhlguEUswi0BgxTA/f/959PfWDrPl059BdVFk/3Josv0"
    "2d+ui3XRTb6JOn+ttXfvrzplnOhhGHFBjXlvv+3HYzv/8yjEFgyh3hAAuAsgdt/1"
    "4/Tk1BtG305e2NootXylmvlpKc1y8nOeuTp/rLrvWoY0bXCLtGDF6a30dipZxeid"
    "vvRZes5ciyf/8yjEHgtolhQACe7Etb00WXLrs7tElf3qpv9UnX8lnz73nCrr74Rd"
    "owYvTau3/9e+radtO+nPpy8M159ON3qeqi9TvZ3ZeSn/8yjEKwxRxhQADcrI3VK0"
    "zvok1TAAwAJB5969NeugvXthFbVBiRtq6uy9ddFvqssUdv2Waujv0Wd+qiz002em"
    "TaAAxXQIB///8yjENAswlh5ICK5U/8v/n9yGKVQ8wmJzB+LOHCcQBgFBxYkMv3T5"
    "DL//uRRb+mzVT/////9a8aRrdPYJg4Ud4nBxKTtHgWP/8yjEQgwosmx/QeACD5Ell"
    "IrF0ELMF9InAGxYGYDgYQmXjEjlIJthZQGqyDjgGbdExZbbZfJwi5mXC+jWvb8zL"
    "hfWgZr/wTH/8yjETBcRWpABi6AAIFxG3/xUWFxUWF//6hdgsuo4kS00iicSgGCtIg"
    "FE4BQAgqwUAjRQCgFhVMUgiSrBZDnuOf1FCzgUVND/8yjEKgxoxcwBxkgBVgbFxNh"
    "PNi1MQU1FMy4xMDBVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVU="
)


class DirectConversationTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "requires ffmpeg")
    async def test_pcm_is_playable_before_tts_eos(self):
        release_eos = asyncio.Event()
        playable = asyncio.Event()
        pcm = bytearray()

        async def stream():
            yield {"type": "audio", "data": _SHORT_MP3}
            await release_eos.wait()

        async def sink(chunk):
            pcm.extend(chunk)
            if len(pcm) >= _PCM_PREBUFFER_BYTES:
                playable.set()

        communicate = lambda *_args, **_kwargs: SimpleNamespace(stream=stream)
        with patch.dict(
            "sys.modules",
            {"edge_tts": SimpleNamespace(Communicate=communicate)},
        ):
            task = asyncio.create_task(
                stream_speech_pcm("answer", "voice", sink)
            )
            try:
                await asyncio.wait_for(playable.wait(), timeout=2)
                self.assertFalse(release_eos.is_set())
            finally:
                release_eos.set()
                await task

        self.assertGreaterEqual(len(pcm), _PCM_PREBUFFER_BYTES)

    async def test_pcm_buffer_applies_backpressure_and_accepts_short_eos(self):
        pcm = DirectPcmBuffer(prebuffer_bytes=4, max_bytes=4)

        await pcm.put(b"abcd")
        blocked = asyncio.create_task(pcm.put(b"ef"))
        await asyncio.sleep(0)
        self.assertFalse(blocked.done())
        self.assertEqual(next(pcm.iter_pcm_chunks()), b"abcd")
        await blocked
        pcm.finish()
        self.assertEqual(list(pcm.iter_pcm_chunks()), [b"ef"])

        short = DirectPcmBuffer(prebuffer_bytes=4, max_bytes=4)
        await short.put(b"\x00\x00")
        short.finish()
        await asyncio.to_thread(short.wait_for_playable)
        self.assertEqual(list(short.iter_pcm_chunks()), [b"\x00\x00"])

    async def test_direct_speech_waits_for_attention_then_streams(self):
        attention_started = asyncio.Event()
        release_attention = asyncio.Event()
        pcm_ready = asyncio.Event()
        release_tts = asyncio.Event()
        playback_started = asyncio.Event()
        tts_finished = asyncio.Event()

        class Runtime:
            def __init__(self):
                self.audio = b""

            async def tell_direct_stream(self, turn_id, pcm):
                if turn_id != "robot:1":
                    raise AssertionError("unexpected turn ID")
                attention_started.set()
                await release_attention.wait()
                await asyncio.to_thread(pcm.wait_for_playable)
                iterator = pcm.iter_pcm_chunks()
                self.audio += await asyncio.to_thread(next, iterator)
                playback_started.set()
                self.audio += b"".join(
                    await asyncio.to_thread(list, iterator)
                )
                return {"gateway_first_audio_frame_sent_ms": 1234}

        async def synthesize(_answer, _voice, sink, **_kwargs):
            await sink(b"a" * _PCM_PREBUFFER_BYTES)
            pcm_ready.set()
            await release_tts.wait()
            await sink(b"tail")
            tts_finished.set()

        runtime = Runtime()
        with patch(
            "gateway.direct_conversation.stream_speech_pcm",
            new=synthesize,
        ):
            task = asyncio.create_task(
                speak_direct_answer(runtime, "robot:1", "answer", "voice")
            )
            await attention_started.wait()
            await pcm_ready.wait()
            self.assertFalse(playback_started.is_set())
            release_attention.set()
            await playback_started.wait()
            self.assertFalse(tts_finished.is_set())
            release_tts.set()
            metrics = await task

        self.assertEqual(
            runtime.audio,
            b"a" * _PCM_PREBUFFER_BYTES + b"tail",
        )
        self.assertIn("tts_first_pcm_ready_ms", metrics)
        self.assertIn("tts_completed_ms", metrics)

    async def test_pcm_failure_after_first_chunk_aborts_the_stream(self):
        pcm = DirectPcmBuffer(prebuffer_bytes=4, max_bytes=8)
        await pcm.put(b"abcd")
        iterator = pcm.iter_pcm_chunks()
        self.assertEqual(next(iterator), b"abcd")
        pcm.fail(RuntimeError("producer failed"))
        with self.assertRaisesRegex(DirectConversationError, "synthesis failed"):
            next(iterator)

    async def test_pcm_buffer_times_out_when_producer_stalls(self):
        self.assertGreater(
            _PCM_PROGRESS_TIMEOUT_SECONDS,
            EDGE_TTS_CONNECT_TIMEOUT_SECONDS
            + EDGE_TTS_RECEIVE_TIMEOUT_SECONDS,
        )
        empty = DirectPcmBuffer(
            prebuffer_bytes=4,
            max_bytes=8,
            progress_timeout_seconds=0.01,
        )
        with self.assertRaisesRegex(
            DirectConversationError,
            "synthesis stalled",
        ):
            await asyncio.to_thread(empty.wait_for_playable)

        pcm = DirectPcmBuffer(
            prebuffer_bytes=4,
            max_bytes=8,
            progress_timeout_seconds=0.01,
        )
        await pcm.put(b"abcd")
        iterator = pcm.iter_pcm_chunks()
        self.assertEqual(next(iterator), b"abcd")
        with self.assertRaisesRegex(
            DirectConversationError,
            "synthesis stalled",
        ):
            await asyncio.to_thread(next, iterator)

    async def test_synthesis_failure_discards_a_ready_pcm_prefix(self):
        allow_attention = asyncio.Event()
        failure_reported = asyncio.Event()

        class Runtime:
            def __init__(self):
                self.opened_pcm = False

            async def tell_direct_stream(self, _turn_id, pcm):
                await allow_attention.wait()
                await asyncio.to_thread(pcm.wait_for_playable)
                self.opened_pcm = True

        async def synthesize(_answer, _voice, sink, *, on_failure):
            await sink(b"a" * 8000)
            error = RuntimeError("synthesis failed")
            on_failure(error)
            failure_reported.set()
            raise error

        runtime = Runtime()
        with patch(
            "gateway.direct_conversation.stream_speech_pcm",
            new=synthesize,
        ):
            task = asyncio.create_task(
                speak_direct_answer(runtime, "robot:1", "answer", "voice")
            )
            await failure_reported.wait()
            allow_attention.set()
            with self.assertRaisesRegex(
                DirectConversationError,
                "direct answer playback failed",
            ) as raised:
                await task

        self.assertFalse(runtime.opened_pcm)
        self.assertIn("tts_first_pcm_ready_ms", raised.exception.metrics)

    async def test_stream_failure_preserves_tts_and_playback_metrics(self):
        class Runtime:
            async def tell_direct_stream(self, _turn_id, pcm):
                await asyncio.to_thread(pcm.wait_for_playable)
                next(pcm.iter_pcm_chunks())
                raise PendingThoughtRuntimeError(
                    "stream audio: RuntimeError",
                    metrics={
                        "attention_completed_ms": 1100,
                        "gateway_first_audio_frame_sent_ms": 1200,
                        "streamed_audio_frames": 1,
                    },
                )

        async def synthesize(_answer, _voice, sink, **_kwargs):
            await sink(b"a" * 8000)
            await asyncio.Event().wait()

        with patch(
            "gateway.direct_conversation.stream_speech_pcm",
            new=synthesize,
        ):
            with self.assertRaises(DirectConversationError) as raised:
                await speak_direct_answer(
                    Runtime(),
                    "robot:1",
                    "answer",
                    "voice",
                )

        self.assertIn("tts_first_pcm_ready_ms", raised.exception.metrics)
        self.assertEqual(
            raised.exception.metrics["gateway_first_audio_frame_sent_ms"],
            1200,
        )

    async def test_mailbox_separates_capture_slot_from_active_turns(self):
        mailbox = VoiceMailbox()

        # Slot full → raises
        first_id = await mailbox.submit(b"first")
        with self.assertRaisesRegex(
            DirectConversationError,
            "another robot turn is pending",
        ):
            await mailbox.submit(b"while-slot-full")

        first = await mailbox.claim(timeout=0)
        self.assertIsNotNone(first)
        self.assertEqual(first.turn_id, first_id)
        self.assertEqual(first.audio, b"first")

        # Slot empty even though first turn is claimed → second submit succeeds
        second_id = await mailbox.submit(b"second")
        second = await mailbox.claim(timeout=0)
        self.assertIsNotNone(second)
        self.assertEqual(second.turn_id, second_id)

        # First turn begins speaking; second cannot speak simultaneously
        self.assertTrue(await mailbox.begin_answer(first_id))
        self.assertFalse(await mailbox.begin_answer(second_id))
        self.assertFalse(await mailbox.begin_answer(first_id))
        self.assertFalse(await mailbox.abandon(first_id))

        await mailbox.finish_answer(first_id)

        # First turn gone; second can now speak
        self.assertTrue(await mailbox.begin_answer(second_id))
        self.assertFalse(await mailbox.abandon(second_id))
        await mailbox.finish_answer(second_id)

        # Abandon works for claimed-but-not-speaking turns
        third_id = await mailbox.submit(b"third")
        await mailbox.claim(timeout=0)
        self.assertTrue(await mailbox.abandon(third_id))
        self.assertFalse(await mailbox.abandon(third_id))

        fourth_id = await mailbox.submit(b"fourth")
        self.assertNotEqual(fourth_id, first_id)
        self.assertNotEqual(fourth_id, second_id)
        self.assertNotEqual(fourth_id, third_id)

    async def test_waiting_capture_ttl_starts_after_active_turn(self):
        mailbox = VoiceMailbox()
        now = 0.0

        with patch(
            "gateway.direct_conversation._monotonic",
            side_effect=lambda: now,
        ):
            first_id = await mailbox.submit(b"first")
            await mailbox.claim(timeout=0)

            now = 1.0
            second_id = await mailbox.submit(b"second")
            now = 122.0
            await mailbox.finish_answer(first_id)

            second = await mailbox.claim(timeout=0)
            self.assertIsNotNone(second)
            self.assertEqual(second.turn_id, second_id)

            now = 123.0
            await mailbox.submit(b"third")
            now = 244.0
            await mailbox.finish_answer(second_id)
            now = 365.0
            self.assertIsNone(await mailbox.claim(timeout=0))

    def test_turn_report_is_content_free_and_derives_latency(self):
        metrics = {
            "capture_started_uptime_us": 1_000_000,
            "capture_stopped_uptime_us": 2_250_000,
            "gateway_capture_started_ms": 1000,
            "gateway_capture_stopped_ms": 2250,
            "gateway_first_audio_frame_sent_ms": 4300,
        }

        with patch(
            "gateway.direct_conversation._unix_ms",
            return_value=6000,
        ):
            report = build_direct_turn_report(
                "robot:1",
                "ok",
                metrics,
                None,
            )

        self.assertEqual(metrics["device_recording_ms"], 1250)
        self.assertEqual(metrics["submit_to_speech_start_ms"], 2050)
        self.assertEqual(metrics["end_to_end_ms"], 5000)
        encoded = json.dumps(report)
        self.assertNotIn("transcript", encoded)
        self.assertNotIn("answer", encoded)


if __name__ == "__main__":
    unittest.main()
