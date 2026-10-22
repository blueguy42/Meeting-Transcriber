import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt import config, settings_api


class OverlayInCaptures(unittest.TestCase):
    def test_off_by_default_and_editable(self):
        self.assertIs(config.defaults()["overlay_in_captures"], False)
        self.assertIn("overlay_in_captures", settings_api.EDITABLE)
        self.assertIn("overlay_in_captures", settings_api._public(config.defaults()))

    def test_only_a_boolean_is_accepted(self):
        self.assertIs(settings_api._check("overlay_in_captures", True), True)
        for bad in ("yes", 1, None):
            with self.assertRaises(ValueError):
                settings_api._check("overlay_in_captures", bad)


if __name__ == "__main__":
    unittest.main()
