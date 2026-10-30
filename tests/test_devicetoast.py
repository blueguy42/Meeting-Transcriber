import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt.devicetoast import DeviceToasts


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


class Messages(unittest.TestCase):
    def test_one_track_says_its_own_name(self):
        c = Clock()
        t = DeviceToasts(c)
        self.assertEqual(t.message("mic", "MacBook Pro Microphone"), [("Mic", "MacBook Pro Microphone")])
        c.t += 10
        self.assertEqual(t.message("system", "USB-C Headphones"), [("System", "USB-C Headphones")])  # long after: its own toast

    def test_both_to_the_same_device_within_two_seconds_is_one_message(self):
        c = Clock()
        t = DeviceToasts(c)
        t.message("mic", "AirPods Pro")
        c.t += 1.2
        self.assertEqual(t.message("system", "AirPods Pro"), [("Mic and System", "AirPods Pro")])

    def test_different_devices_within_two_seconds_are_one_toast_with_two_lines(self):
        c = Clock()
        t = DeviceToasts(c)
        t.message("system", "USB-C Headphones")
        c.t += 0.8
        self.assertEqual(t.message("mic", "MacBook Pro Microphone"), [("Mic", "MacBook Pro Microphone"), ("System", "USB-C Headphones")])

    def test_too_late_is_not_merged(self):
        c = Clock()
        t = DeviceToasts(c)
        t.message("system", "AirPods Pro")
        c.t += 5
        self.assertEqual(t.message("mic", "AirPods Pro"), [("Mic", "AirPods Pro")])


class RecorderCallback(unittest.TestCase):
    def test_a_failing_callback_never_breaks_the_recorder(self):
        from mt.recording import Recorder

        r = Recorder.__new__(Recorder)
        got = []
        r.on_device_change = lambda track, name: got.append((track, name))
        r._device_changed("mic", "X")
        r._device_changed("system", None)  # no name: nothing to say
        self.assertEqual(got, [("mic", "X")])
        r.on_device_change = lambda *a: 1 / 0
        r._device_changed("mic", "Y")


if __name__ == "__main__":
    unittest.main()
