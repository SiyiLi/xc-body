from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
HEADER = (
    ROOT
    / "firmware"
    / "main"
    / "audio"
    / "prepared_audio_drain.h"
)


@unittest.skipUnless(shutil.which("c++"), "requires a C++ compiler")
class PreparedAudioDrainTests(unittest.TestCase):
    def run_case(self, body: str) -> None:
        source = textwrap.dedent(
            f"""
            #include <cassert>
            #include <chrono>
            #include "{HEADER}"

            using namespace std::chrono_literals;

            PreparedAudioDrainSnapshot CompleteSnapshot(
                    std::size_t packets) {{
                PreparedAudioDrainSnapshot snapshot;
                snapshot.generation_current = true;
                snapshot.decode_queue_empty = true;
                snapshot.playback_queue_empty = true;
                snapshot.received_packets = packets;
                snapshot.decoded_packets = packets;
                snapshot.output_frames = packets;
                return snapshot;
            }}

            int main() {{
            {textwrap.indent(textwrap.dedent(body), "    ")}
                return 0;
            }}
            """
        )
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            source_path = directory_path / "prepared_audio_drain_test.cc"
            binary_path = directory_path / "prepared_audio_drain_test"
            source_path.write_text(source)
            subprocess.run(
                [
                    "c++",
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(source_path),
                    "-o",
                    str(binary_path),
                ],
                check=True,
            )
            subprocess.run([str(binary_path)], check=True)

    def test_slow_output_resets_the_stall_deadline(self):
        self.run_case(
            """
            auto now = PreparedAudioStallDeadline::Clock::time_point{};
            PreparedAudioStallDeadline deadline(0, now, 5s);
            for (std::size_t frames = 1; frames <= 3; ++frames) {
                now += 4s;
                deadline.ObserveOutput(frames, now);
                assert(!deadline.Expired(now + 4s));
            }
            assert(now.time_since_epoch() > 5s);
            auto snapshot = CompleteSnapshot(3);
            assert(EvaluatePreparedAudioDrain(snapshot) ==
                   PreparedAudioDrainDecision::kComplete);
            """
        )

    def test_queued_audio_without_output_progress_stalls(self):
        self.run_case(
            """
            auto now = PreparedAudioStallDeadline::Clock::time_point{};
            PreparedAudioStallDeadline deadline(4, now, 5s);
            auto snapshot = CompleteSnapshot(5);
            snapshot.output_frames = 4;
            snapshot.playback_queue_empty = false;
            assert(EvaluatePreparedAudioDrain(snapshot) ==
                   PreparedAudioDrainDecision::kWait);
            assert(!deadline.Expired(now + 4999ms));
            assert(deadline.Expired(now + 5s));
            """
        )

    def test_empty_queues_do_not_hide_incomplete_accounting(self):
        self.run_case(
            """
            auto snapshot = CompleteSnapshot(5);
            snapshot.decoded_packets = 4;
            snapshot.output_frames = 4;
            assert(EvaluatePreparedAudioDrain(snapshot) ==
                   PreparedAudioDrainDecision::kWait);
            """
        )

    def test_only_current_generation_counts_as_prepared_audio(self):
        self.run_case(
            """
            assert(IsCurrentPreparedAudioGeneration(7, 7, true));
            assert(!IsCurrentPreparedAudioGeneration(0, 7, true));
            assert(!IsCurrentPreparedAudioGeneration(6, 7, true));
            assert(!IsCurrentPreparedAudioGeneration(7, 7, false));
            """
        )

if __name__ == "__main__":
    unittest.main()
