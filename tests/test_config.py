"""Tests for config.py's profile persistence, using a temp directory
in place of the real ~/.config so this doesn't touch the actual
filesystem or leave test artifacts behind.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwcombine import config
from pwcombine.models import Profile, SlaveSpec


class TestProfilePersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_environ = dict(__import__("os").environ)
        __import__("os").environ["XDG_CONFIG_HOME"] = self._tmp.name

    def tearDown(self):
        import os

        os.environ.clear()
        os.environ.update(self._orig_environ)
        self._tmp.cleanup()

    def test_load_profiles_empty_when_no_file(self):
        self.assertEqual(config.load_profiles(), {})

    def test_save_and_reload_round_trip(self):
        profile = Profile(
            name="pc_plus_bt",
            slaves=[
                SlaveSpec(sink_name="alsa_output.builtin", latency_offset_us=0),
                SlaveSpec(sink_name="bluez_output.speaker1", latency_offset_us=90000),
            ],
            combined_sink_name="combined_pc_plus_bt",
        )
        config.save_profile(profile)

        reloaded = config.load_profiles()
        self.assertIn("pc_plus_bt", reloaded)
        restored = reloaded["pc_plus_bt"]
        self.assertEqual(restored.combined_sink_name, "combined_pc_plus_bt")
        self.assertEqual(len(restored.slaves), 2)
        self.assertEqual(restored.slaves[1].latency_offset_us, 90000)

    def test_save_multiple_profiles_preserves_all(self):
        config.save_profile(Profile(name="a", slaves=[SlaveSpec(sink_name="x")]))
        config.save_profile(Profile(name="b", slaves=[SlaveSpec(sink_name="y")]))
        profiles = config.load_profiles()
        self.assertEqual(set(profiles.keys()), {"a", "b"})

    def test_overwrite_existing_profile_by_name(self):
        config.save_profile(Profile(name="a", slaves=[SlaveSpec(sink_name="x")]))
        config.save_profile(Profile(name="a", slaves=[SlaveSpec(sink_name="z")]))
        profiles = config.load_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles["a"].slaves[0].sink_name, "z")

    def test_delete_profile(self):
        config.save_profile(Profile(name="a", slaves=[SlaveSpec(sink_name="x")]))
        deleted = config.delete_profile("a")
        self.assertTrue(deleted)
        self.assertEqual(config.load_profiles(), {})

    def test_delete_nonexistent_profile_returns_false(self):
        self.assertFalse(config.delete_profile("does_not_exist"))

    def test_corrupted_file_raises_config_error(self):
        path = config._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(config.ConfigError):
            config.load_profiles()

    def test_single_corrupted_entry_does_not_break_others(self):
        path = config._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"profiles": [{"name": "good", "slaves": []}, {"slaves": []}]}',
            encoding="utf-8",
        )
        profiles = config.load_profiles()
        self.assertEqual(list(profiles.keys()), ["good"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
