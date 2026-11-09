import unittest
from unittest import mock

from meeting_transcriber.recording import device_watcher
from meeting_transcriber.recording.device_watcher import DeviceWatcher


class Watcher(unittest.TestCase):
    def setUp(self):
        self.ids = {"in": 1, "out": 10}
        names = {1: "MacBook Mic", 2: "AirPods", 10: "Speakers", 11: "AirPods"}
        for target, fn in (("default_input_id", lambda: self.ids["in"]), ("default_output_id", lambda: self.ids["out"]), ("device_name", names.get)):
            p = mock.patch.object(device_watcher, target, fn)
            p.start()
            self.addCleanup(p.stop)
        self.seen = []
        self.recording = False
        self.watcher = DeviceWatcher(lambda track, name: self.seen.append((track, name)), active=lambda: not self.recording)

    def test_nothing_is_reported_until_a_device_changes(self):
        self.watcher.poll()
        self.assertEqual(self.seen, [])

    def test_a_switch_is_reported_once_with_the_new_name(self):
        self.ids["in"] = 2
        self.watcher.poll()
        self.watcher.poll()
        self.assertEqual(self.seen, [("mic", "AirPods")])
        self.ids["out"] = 11
        self.watcher.poll()
        self.assertEqual(self.seen[-1], ("system", "AirPods"))

    def test_while_recording_the_recorder_reports_so_this_stays_quiet_and_does_not_report_late(self):
        self.recording = True
        self.ids["in"] = 2
        self.watcher.poll()
        self.recording = False
        self.watcher.poll()  # the switch happened during the recording: not news any more
        self.assertEqual(self.seen, [])

    def test_a_device_that_disappears_is_not_a_switch(self):
        self.ids["in"] = None
        self.watcher.poll()
        self.assertEqual(self.seen, [])


if __name__ == "__main__":
    unittest.main()
