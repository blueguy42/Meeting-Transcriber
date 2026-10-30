import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt.recording.device_events import merged_events


class Merged(unittest.TestCase):
    rel = staticmethod(lambda t: t - 100.0)  # recording seconds

    def test_the_same_device_within_two_seconds_is_one_mic_and_system_switch(self):
        mic = [{"t": 134.2, "event": "switched", "device": "AirPods"}]
        system = [{"t": 135.0, "event": "switched", "device": "AirPods"}]
        out = merged_events(mic, system, self.rel)
        self.assertEqual(out, [{"at": 34.2, "event": "switched", "device": "AirPods", "track": "mic+system"}])

    def test_different_devices_or_too_far_apart_stay_separate_and_ordered(self):
        mic = [{"t": 132.0, "event": "switched", "device": "MacBook Pro Microphone"}, {"t": 140.0, "event": "stalled", "device": None}]
        system = [{"t": 133.0, "event": "switched", "device": "Speakers"}]
        out = merged_events(mic, system, self.rel)
        self.assertEqual([(e["at"], e["track"]) for e in out], [(32.0, "mic"), (33.0, "system"), (40.0, "mic")])
        self.assertNotIn("device", out[2])  # empty values are left out


if __name__ == "__main__":
    unittest.main()
