"""End-to-end smoke test: drives the actual interactive menu loop
(cli.run) with scripted stdin answers, while pactl itself is mocked
at the subprocess boundary. This is the closest thing to "run the
real tool" available without live PipeWire hardware.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwcombine import cli

SAMPLE_PATH = Path(__file__).resolve().parent / "sample_sinks.json"
SAMPLE_SINKS_JSON = SAMPLE_PATH.read_text(encoding="utf-8")
DEFAULT_INFO_JSON = '{"default_sink_name": "alsa_output.pci-0000_00_1f.3.analog-stereo"}'


def _fake_pactl_dispatch(args, capture_output, text, timeout, check):
    """Mimics real pactl by branching on the subcommand, so the CLI's
    successive calls (list sinks, info, load-module, set-default-sink)
    each get a plausible response instead of one canned answer.

    Note: args[0] is the 'pactl' binary name itself (pactl_client._run
    builds [PACTL_BIN, *args]), so subcommand tokens start at args[1].
    """
    sub = args[1:]
    if sub[:3] == ["-f", "json", "list"]:
        return subprocess.CompletedProcess(args, 0, stdout=SAMPLE_SINKS_JSON, stderr="")
    if sub[:3] == ["-f", "json", "info"]:
        return subprocess.CompletedProcess(args, 0, stdout=DEFAULT_INFO_JSON, stderr="")
    if sub[0] == "load-module":
        return subprocess.CompletedProcess(args, 0, stdout="101\n", stderr="")
    if sub[0] == "set-default-sink":
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    if sub[0] == "unload-module":
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


class TestCliEndToEnd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        import os

        self._orig_environ = dict(os.environ)
        os.environ["XDG_CONFIG_HOME"] = self._tmp.name

    def tearDown(self):
        import os

        os.environ.clear()
        os.environ.update(self._orig_environ)
        self._tmp.cleanup()

    def _run_with_scripted_input(self, scripted_lines: list[str]) -> str:
        """Runs cli.run() with stdin fed from scripted_lines and pactl
        mocked, returning everything printed to stdout."""
        stdin_text = "\n".join(scripted_lines) + "\n"
        with mock.patch("subprocess.run", side_effect=_fake_pactl_dispatch), mock.patch(
            "shutil.which", return_value="/usr/bin/pactl"
        ), mock.patch("sys.stdin", io.StringIO(stdin_text)), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as fake_stdout:
            cli.run()
        return fake_stdout.getvalue()

    def test_create_combined_sink_end_to_end(self):
        # Menu choice 1 (create) -> select sinks 1,2 -> skip latency
        # tuning -> name the sink -> don't save a profile -> exit.
        output = self._run_with_scripted_input(
            [
                "1",  # create new
                "1,2",  # select internal speaker + BT speaker 1
                "n",  # skip latency tuning
                "Test Combo",  # display name
                "n",  # don't save profile
                "0",  # exit
            ]
        )
        self.assertIn("Created 'Test Combo'", output)
        self.assertIn("set as default", output)

    def test_create_then_save_profile_end_to_end(self):
        output = self._run_with_scripted_input(
            [
                "1",  # create new
                "1,2",  # select internal + BT1
                "n",  # skip latency tuning
                "My Combo",  # display name
                "y",  # yes, save profile
                "my_combo",  # profile name
                "0",  # exit
            ]
        )
        self.assertIn("Created 'My Combo'", output)
        self.assertIn("Saved profile 'my_combo'", output)

        from pwcombine import config

        profiles = config.load_profiles()
        self.assertIn("my_combo", profiles)
        self.assertEqual(len(profiles["my_combo"].slaves), 2)

    def test_load_profile_with_missing_sink_warns_but_continues(self):
        """End-to-end version of the 'saved BT speaker not connected'
        requirement: profile references a sink absent from the live
        (mocked) sink list, plus one that IS present."""
        from pwcombine import config
        from pwcombine.models import Profile, SlaveSpec

        config.save_profile(
            Profile(
                name="partial_combo",
                slaves=[
                    SlaveSpec(sink_name="alsa_output.pci-0000_00_1f.3.analog-stereo"),
                    SlaveSpec(sink_name="bluez_output.NOT_HERE.1"),
                    SlaveSpec(sink_name="bluez_output.AA_BB_CC_11_22_33.1"),
                ],
            )
        )

        output = self._run_with_scripted_input(
            [
                "2",  # load profile
                "1",  # choose the only profile
                "0",  # exit
            ]
        )
        self.assertIn("not currently available", output)
        self.assertIn("bluez_output.NOT_HERE.1", output)
        self.assertIn("Loaded 'partial_combo'", output)

    def test_unload_combined_sink_end_to_end(self):
        output = self._run_with_scripted_input(
            [
                "5",  # unload
                "1",  # the one combined sink in the sample data
                "0",  # exit
            ]
        )
        self.assertIn("Unloaded", output)

    def test_retune_latency_after_create_end_to_end(self):
        """Create a combined sink, then retune its latency (option 6)
        without recreating it. The retune should call
        set-sink-latency-offset per slave and report success."""
        calls = []

        def _tracking_dispatch(args, capture_output, text, timeout, check):
            calls.append(list(args[1:]))
            return _fake_pactl_dispatch(args, capture_output, text, timeout, check)

        stdin_text = "\n".join(
            [
                "1",  # create new
                "1,2",  # select internal + BT1
                "n",  # skip latency tuning at creation
                # Name chosen so sink_name_from_display() -> "combined_pc_plus_bt",
                # which already exists in sample_sinks.json, satisfying the
                # retune liveness check against the (mocked) live sink list.
                "pc plus bt",  # display name
                "n",  # don't save profile
                "6",  # retune latency
                "50000",  # new offset for slave 1
                "",  # keep slave 2's current offset
                "0",  # exit
            ]
        ) + "\n"

        with mock.patch("subprocess.run", side_effect=_tracking_dispatch), mock.patch(
            "shutil.which", return_value="/usr/bin/pactl"
        ), mock.patch("sys.stdin", io.StringIO(stdin_text)), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as fake_stdout:
            cli.run()
        output = fake_stdout.getvalue()

        self.assertIn("Latency offsets applied", output)
        offset_calls = [c for c in calls if c and c[0] == "set-sink-latency-offset"]
        self.assertEqual(len(offset_calls), 2)
        # First slave got the new 50000 value we typed.
        self.assertIn("50000", offset_calls[0])

    def test_retune_without_active_sink_warns(self):
        """Option 6 with nothing created/loaded this session should warn,
        not crash or call pactl for offsets."""
        output = self._run_with_scripted_input(["6", "0"])
        self.assertIn("No combined sink from this session", output)

    def test_invalid_menu_choice_does_not_crash(self):
        output = self._run_with_scripted_input(["99", "0"])
        self.assertIn("Not a valid option", output)

    def test_selecting_only_one_sink_refuses_to_combine(self):
        output = self._run_with_scripted_input(
            [
                "1",
                "1",  # only one sink selected
                "0",
            ]
        )
        self.assertIn("Need at least two sinks", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
