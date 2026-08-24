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
    / "boards"
    / "common"
    / "power_save_state.h"
)
@unittest.skipUnless(shutil.which("c++"), "requires a C++ compiler")
class PowerSaveStateTests(unittest.TestCase):
    def test_dim_off_wake_and_external_power_policy(self):
        source = textwrap.dedent(
            f"""
            #include <cassert>
            #include "{HEADER}"

            int main() {{
                PowerSaveState state(6);
                assert(!state.enabled());
                assert(state.Tick(true) == PowerSaveTransition::NONE);

                assert(state.SetEnabled(true) == PowerSaveTransition::NONE);
                assert(state.Tick(true) == PowerSaveTransition::NONE);
                assert(state.Tick(true) == PowerSaveTransition::NONE);
                assert(
                    state.Tick(true) == PowerSaveTransition::ENTER_DIM
                );
                assert(state.dimmed() && !state.sleeping());
                assert(state.WakeUp() == PowerSaveTransition::EXIT_DIM);
                assert(!state.dimmed() && !state.sleeping());

                for (int i = 0; i < 3; ++i) {{
                    state.Tick(true);
                }}
                assert(state.dimmed());
                for (int i = 0; i < 3; ++i) {{
                    state.Tick(true);
                }}
                assert(state.sleeping());
                assert(state.WakeUp() == PowerSaveTransition::EXIT_SLEEP);
                assert(!state.dimmed() && !state.sleeping());

                state.Tick(true);
                state.Tick(true);
                assert(
                    state.SetEnabled(false) == PowerSaveTransition::NONE
                );
                assert(!state.enabled() && state.ticks() == 0);

                state.SetEnabled(true);
                state.Tick(true);
                state.Tick(true);
                state.Tick(true);
                assert(state.dimmed());
                assert(
                    state.Tick(false) == PowerSaveTransition::EXIT_DIM
                );
                assert(state.ticks() == 0);
                return 0;
            }}
            """
        )
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            source_path = directory_path / "power_save_state_test.cc"
            binary_path = directory_path / "power_save_state_test"
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

if __name__ == "__main__":
    unittest.main()
