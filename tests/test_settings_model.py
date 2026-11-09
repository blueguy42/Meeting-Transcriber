import unittest
from pathlib import Path

from meeting_transcriber.settings import api, config
from meeting_transcriber.settings.model import Settings


class Model(unittest.TestCase):
    def test_what_config_toml_does_not_set_keeps_its_default(self):
        s = Settings.from_toml({"language": "en", "diarize": False})
        self.assertEqual((s.language, s.diarize), ("en", False))
        self.assertEqual(s.model, Settings().model)

    def test_a_key_nobody_reads_is_ignored_and_reported(self):
        with self.assertLogs("meeting_transcriber.settings.model", "WARNING") as logs:
            s = Settings.from_toml({"languge": "en", "language": "id"})
        self.assertEqual(s.language, "id")
        self.assertIn("languge", logs.output[0])

    def test_the_meetings_folder_is_a_path_with_the_home_folder_filled_in(self):
        self.assertEqual(Settings(meetings_dir="~/Documents/Meetings").meetings_dir, Path.home() / "Documents" / "Meetings")
        self.assertIsInstance(Settings.from_toml({"meetings_dir": "~/x"}).meetings_dir, Path)

    def test_lists_are_not_shared_between_instances(self):
        a, b = Settings(), Settings()
        a.vocabulary.append("Budi")
        self.assertEqual(b.vocabulary, [])

    def test_replace_changes_a_copy(self):
        a = Settings()
        b = a.replace(vocabulary=["Budi"], model="x/y")
        self.assertEqual((a.vocabulary, b.vocabulary, b.model), ([], ["Budi"], "x/y"))

    def test_every_setting_the_window_edits_exists(self):
        names = set(Settings().to_dict())
        self.assertLessEqual(set(api.EDITABLE), names)
        self.assertEqual(set(api._public(config.defaults())), set(api.EDITABLE))


if __name__ == "__main__":
    unittest.main()
