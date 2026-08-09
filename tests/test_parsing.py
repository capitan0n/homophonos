"""Tests for pactl_client's JSON parsing, using recorded/synthetic
pactl output instead of a live audio server (none is available in CI
or in this sandbox). subprocess.run is monkeypatched so these exercise
the real parsing code path.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwcombine import combiner, pactl_client
from pwcombine.models import Profile, SlaveSpec

SAMPLE_PATH = Path(__file__).resolve().parent / "sample_sinks.json"


def _fake_completed_process(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TestListSinks(unittest.TestCase):
    def test_parses_well_formed_sinks(self):
        sample = SAMPLE_PATH.read_text(encoding="utf-8")
        with mock.patch("subprocess.run", return_value=_fake_completed_process(sample)):
            sinks = pactl_client.list_sinks()

        self.assertEqual(len(sinks), 4)
        names = {s.name for s in sinks}
        self.assertIn("alsa_output.pci-0000_00_1f.3.analog-stereo", names)
        self.assertIn("bluez_output.AA_BB_CC_11_22_33.1", names)

    def test_identifies_bluetooth_sinks(self):
        sample = SAMPLE_PATH.read_text(encoding="utf-8")
        with mock.patch("subprocess.run", return_value=_fake_completed_process(sample)):
            sinks = pactl_client.list_sinks()

        bt_sinks = [s for s in sinks if s.is_bluetooth]
        self.assertEqual(len(bt_sinks), 2)
        self.assertTrue(all(s.name.startswith("bluez_") for s in bt_sinks))

    def test_identifies_combined_sink_by_name_prefix_and_driver(self):
        sample = SAMPLE_PATH.read_text(encoding="utf-8")
        with mock.patch("subprocess.run", return_value=_fake_completed_process(sample)):
            sinks = pactl_client.list_sinks()

        combined = [s for s in sinks if s.is_combined]
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0].name, "combined_pc_plus_bt")
        self.assertEqual(combined[0].module_index, 99)

    def test_skips_malformed_entries_without_crashing(self):
        malformed = json.dumps(
            [
                {"index": 1, "name": "good_sink", "description": "Good Sink"},
                {"description": "missing index and name entirely"},
                {"index": "not-an-int", "name": "bad_index_sink"},
            ]
        )
        with mock.patch("subprocess.run", return_value=_fake_completed_process(malformed)):
            sinks = pactl_client.list_sinks()

        # Only the well-formed entry should survive; the malformed
        # ones are skipped rather than raising.
        self.assertEqual(len(sinks), 1)
        self.assertEqual(sinks[0].name, "good_sink")

    def test_raises_clear_error_on_invalid_json(self):
        with mock.patch("subprocess.run", return_value=_fake_completed_process("not json {{{")):
            with self.assertRaises(pactl_client.PactlError):
                pactl_client.list_sinks()

    def test_raises_on_missing_binary(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(pactl_client.PactlError):
                pactl_client.list_sinks()

    def test_raises_on_nonzero_exit(self):
        proc = _fake_completed_process("", returncode=1)
        proc.stderr = "connection refused"
        with mock.patch("subprocess.run", return_value=proc):
            with self.assertRaises(pactl_client.PactlError):
                pactl_client.list_sinks()


class TestCombinerLogic(unittest.TestCase):
    def setUp(self):
        sample = SAMPLE_PATH.read_text(encoding="utf-8")
        with mock.patch("subprocess.run", return_value=_fake_completed_process(sample)):
            self.live_sinks = pactl_client.list_sinks()

    def test_get_real_sinks_excludes_combined(self):
        real = combiner.get_real_sinks(self.live_sinks)
        self.assertEqual(len(real), 3)
        self.assertTrue(all(not s.is_combined for s in real))

    def test_resolve_profile_all_present(self):
        profile = Profile(
            name="pc_plus_bt",
            slaves=[
                SlaveSpec(sink_name="alsa_output.pci-0000_00_1f.3.analog-stereo"),
                SlaveSpec(sink_name="bluez_output.AA_BB_CC_11_22_33.1", latency_offset_us=80000),
            ],
        )
        resolved = combiner.resolve_profile(profile, self.live_sinks)
        self.assertTrue(resolved.is_fully_available)
        self.assertTrue(resolved.can_create)
        self.assertEqual(len(resolved.available), 2)
        self.assertEqual(len(resolved.missing), 0)

    def test_resolve_profile_missing_bluetooth_speaker(self):
        """Simulates the exact scenario in the requirements: a saved BT
        speaker that isn't currently connected/powered on."""
        profile = Profile(
            name="pc_plus_bt",
            slaves=[
                SlaveSpec(sink_name="alsa_output.pci-0000_00_1f.3.analog-stereo"),
                SlaveSpec(sink_name="bluez_output.NOT_CONNECTED.1"),
            ],
        )
        resolved = combiner.resolve_profile(profile, self.live_sinks)
        self.assertFalse(resolved.is_fully_available)
        self.assertEqual(len(resolved.missing), 1)
        self.assertEqual(resolved.missing[0].sink_name, "bluez_output.NOT_CONNECTED.1")
        # Only one sink available -> can't form a combined sink from it alone.
        self.assertEqual(len(resolved.available), 1)
        self.assertFalse(len(resolved.available) >= 2)

    def test_resolve_profile_nothing_present(self):
        profile = Profile(
            name="ghost_profile",
            slaves=[
                SlaveSpec(sink_name="bluez_output.GONE_1.1"),
                SlaveSpec(sink_name="bluez_output.GONE_2.1"),
            ],
        )
        resolved = combiner.resolve_profile(profile, self.live_sinks)
        self.assertFalse(resolved.can_create)

    def test_sink_name_from_display_sanitizes(self):
        self.assertEqual(
            combiner.sink_name_from_display("PC + Lenovo Speakers!"),
            "combined_pc_lenovo_speakers",
        )
        self.assertEqual(combiner.sink_name_from_display("   "), "combined_combo")

    def test_create_combined_sink_requires_two_slaves(self):
        with self.assertRaises(combiner.CombinerError):
            combiner.create_combined_sink(
                "Solo", [SlaveSpec(sink_name="only_one")], set_as_default=False
            )

    def test_unique_sink_name_avoids_collision(self):
        # Base name free -> returned as-is.
        self.assertEqual(
            combiner.unique_sink_name("Combined Output", set()),
            "combined_combined_output",
        )
        # Base taken -> suffixed; next taken too -> next suffix.
        existing = {"combined_combined_output", "combined_combined_output_2"}
        self.assertEqual(
            combiner.unique_sink_name("Combined Output", existing),
            "combined_combined_output_3",
        )


class TestModuleLoadArgFormatting(unittest.TestCase):
    """Verifies the fix for space-containing descriptions: they must
    stay in a single argv element, not get shell-style split."""

    def test_load_combine_sink_module_argv_shape(self):
        captured_args = {}

        def fake_run(args, capture_output, text, timeout, check):
            captured_args["args"] = args
            return _fake_completed_process("123")

        with mock.patch("subprocess.run", side_effect=fake_run):
            module_index = pactl_client.load_combine_sink_module(
                sink_name="combined_pc_plus_bt",
                display_name="PC + Lenovo Speakers",
                member_sink_names=["alsa_output.foo", "bluez_output.bar"],
            )

        self.assertEqual(module_index, 123)
        args = captured_args["args"]
        # The description with spaces must be exactly one argv item.
        desc_items = [a for a in args if "device.description" in a]
        self.assertEqual(len(desc_items), 1)
        self.assertEqual(desc_items[0], 'sink_properties=device.description="PC + Lenovo Speakers"')
        # PipeWire uses sinks= (not PulseAudio's slaves=), one item, comma-joined.
        sink_items = [a for a in args if a.startswith("sinks=")]
        self.assertEqual(len(sink_items), 1)
        self.assertEqual(sink_items[0], "sinks=alsa_output.foo,bluez_output.bar")
        # And the legacy slaves= must NOT be present.
        self.assertEqual([a for a in args if a.startswith("slaves=")], [])

    def test_load_combine_sink_module_escapes_quotes_in_description(self):
        """A double quote or backslash in the user's label must be
        escaped so it can't terminate the quoted property value early."""
        captured_args = {}

        def fake_run(args, capture_output, text, timeout, check):
            captured_args["args"] = args
            return _fake_completed_process("123")

        with mock.patch("subprocess.run", side_effect=fake_run):
            pactl_client.load_combine_sink_module(
                sink_name="combined_x",
                display_name='Weird" \\ name',
                member_sink_names=["a", "b"],
            )

        desc_items = [a for a in captured_args["args"] if "device.description" in a]
        self.assertEqual(len(desc_items), 1)
        # The raw quote/backslash must not appear unescaped inside the value.
        self.assertEqual(
            desc_items[0],
            'sink_properties=device.description="Weird\\" \\\\ name"',
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
