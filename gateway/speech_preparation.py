"""Prepare validated Opus speech without retaining its plaintext source."""

from __future__ import annotations

import asyncio
import base64
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from gateway.pending_thought import PendingThoughtError, decode_prepared_audio

VOICE_ENV = "XC_BODY_VOICE"
DEFAULT_VOICE = "zh-CN-YunxiNeural"
PCM_SAMPLE_RATE = 16_000
PCM_CHUNK_BYTES = 4_096
EDGE_TTS_CONNECT_TIMEOUT_SECONDS = 10
EDGE_TTS_RECEIVE_TIMEOUT_SECONDS = 60
PcmSink = Callable[[bytes], Awaitable[None]]
PcmFailureSink = Callable[[Exception], None]


class SpeechPreparationError(RuntimeError):
    """Transient speech preparation could not produce accepted audio."""


def _opus_packets(ogg: bytes) -> list[bytes]:
    """Extract complete Opus packets from an Ogg stream."""

    offset = 0
    partial = bytearray()
    packets: list[bytes] = []
    while offset < len(ogg):
        if len(ogg) - offset < 27 or ogg[offset : offset + 4] != b"OggS":
            raise SpeechPreparationError("TTS encoder returned invalid Ogg")
        segment_count = ogg[offset + 26]
        table_start = offset + 27
        table_end = table_start + segment_count
        if table_end > len(ogg):
            raise SpeechPreparationError("TTS encoder returned invalid Ogg")
        lacing = ogg[table_start:table_end]
        data_start = table_end
        data_end = data_start + sum(lacing)
        if data_end > len(ogg):
            raise SpeechPreparationError("TTS encoder returned invalid Ogg")
        cursor = data_start
        for size in lacing:
            partial.extend(ogg[cursor : cursor + size])
            cursor += size
            if size < 255:
                packet = bytes(partial)
                partial.clear()
                if not packet.startswith((b"OpusHead", b"OpusTags")):
                    packets.append(packet)
        offset = data_end
    if partial or not packets:
        raise SpeechPreparationError("TTS encoder returned incomplete Opus")
    return packets


def _frame_packets(packets: Sequence[bytes]) -> bytes:
    """Frame and validate raw Opus packets for the robot playback contract."""

    framed = bytearray()
    for packet in packets:
        if not packet or len(packet) > 1275:
            raise SpeechPreparationError("TTS encoder returned invalid Opus")
        framed.extend(len(packet).to_bytes(2, "big"))
        framed.extend(packet)
    encoded = base64.b64encode(framed).decode("ascii")
    try:
        decode_prepared_audio(encoded)
    except PendingThoughtError as exc:
        raise SpeechPreparationError(str(exc)) from exc
    return bytes(framed)


async def prepare_speech(summary: str, voice: str) -> str:
    """Synthesize and normalize one summary into validated prepared audio."""

    try:
        import edge_tts
    except ImportError as exc:
        raise SpeechPreparationError(
            "edge-tts is not installed in the pending service environment"
        ) from exc
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SpeechPreparationError("ffmpeg is required for speech prep")
    with tempfile.TemporaryDirectory(prefix="xc-body-voice-") as temp_dir:
        encoded = Path(temp_dir) / "speech.ogg"
        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-af",
            "loudnorm=I=-16:TP=-2:LRA=7",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libopus",
            "-application",
            "voip",
            "-b:a",
            "32k",
            "-frame_duration",
            "60",
            str(encoded),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stderr is not None
        stderr_task = asyncio.create_task(process.stderr.read())
        synthesis_error: Exception | None = None
        try:
            async for message in edge_tts.Communicate(
                summary,
                voice,
                connect_timeout=EDGE_TTS_CONNECT_TIMEOUT_SECONDS,
                receive_timeout=EDGE_TTS_RECEIVE_TIMEOUT_SECONDS,
            ).stream():
                if message["type"] != "audio":
                    continue
                process.stdin.write(message["data"])
                await process.stdin.drain()
        except Exception as exc:
            synthesis_error = exc
        finally:
            process.stdin.close()
        await process.wait()
        stderr = await stderr_task
        if synthesis_error is not None and not (
            isinstance(synthesis_error, (BrokenPipeError, ConnectionResetError))
            and process.returncode != 0
        ):
            raise SpeechPreparationError(
                f"speech synthesis failed ({type(synthesis_error).__name__})"
            ) from synthesis_error
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise SpeechPreparationError(
                f"speech encoding failed: {detail or process.returncode}"
            )
        return base64.b64encode(
            _frame_packets(_opus_packets(encoded.read_bytes()))
        ).decode("ascii")


async def stream_speech_pcm(
    summary: str,
    voice: str,
    sink: PcmSink,
    *,
    on_failure: PcmFailureSink | None = None,
) -> None:
    """Stream 16 kHz mono PCM to ``sink`` with backpressure."""

    try:
        import edge_tts
    except ImportError as exc:
        raise SpeechPreparationError(
            "edge-tts is not installed in the pending service environment"
        ) from exc
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SpeechPreparationError("ffmpeg is required for speech prep")
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-probesize",
        "32",
        "-analyzeduration",
        "0",
        "-f",
        "mp3",
        "-i",
        "pipe:0",
        "-ac",
        "1",
        "-ar",
        str(PCM_SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    async def forward_pcm() -> None:
        while chunk := await process.stdout.read(PCM_CHUNK_BYTES):
            await sink(chunk)

    pcm_task = asyncio.create_task(forward_pcm())
    stderr_task = asyncio.create_task(process.stderr.read())
    try:
        try:
            async for message in edge_tts.Communicate(
                summary,
                voice,
                connect_timeout=EDGE_TTS_CONNECT_TIMEOUT_SECONDS,
                receive_timeout=EDGE_TTS_RECEIVE_TIMEOUT_SECONDS,
            ).stream():
                if message["type"] != "audio":
                    continue
                if pcm_task.done():
                    pcm_task.result()
                process.stdin.write(message["data"])
                await process.stdin.drain()
        except Exception as exc:
            error = SpeechPreparationError(
                f"speech synthesis failed ({type(exc).__name__})"
            )
            if on_failure is not None:
                on_failure(error)
            raise error from exc
        finally:
            process.stdin.close()
        await process.wait()
        stderr = await stderr_task
        await pcm_task
    except BaseException:
        if process.returncode is None:
            process.terminate()
            await process.wait()
        await asyncio.gather(pcm_task, stderr_task, return_exceptions=True)
        raise

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        error = SpeechPreparationError(
            f"speech encoding failed: {detail or process.returncode}"
        )
        if on_failure is not None:
            on_failure(error)
        raise error
