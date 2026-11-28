"""How many people there are is worked out from the meeting itself: voice groups are merged up to the middle of the widest gap between
"the same person" distances and "a different person" distances (a fixed distance is above that gap in some meetings and below it in others)."""

import math
import unittest

import numpy as np

from meeting_transcriber.transcription import diarize


def voice(angle_degrees: float, seconds: float = 30.0):
    """A voiceprint at an angle on a circle: two of them are 1 - cos(difference) apart, like the app's cosine distance."""
    a = math.radians(angle_degrees)
    return np.array([math.cos(a), math.sin(a)]) * seconds, seconds


def distance(a: float, b: float) -> float:
    return 1 - math.cos(math.radians(a - b))


class AutomaticCut(unittest.TestCase):
    def test_the_cut_is_the_middle_of_the_widest_gap(self):
        merges = [0.07, 0.08, 0.12, 0.19, 0.33, 0.34, 0.47, 0.58]  # a meeting where two different people join at 0.33
        self.assertAlmostEqual(diarize.automatic_distance(merges), (0.19 + 0.33) / 2)

    def test_one_person_is_one_speaker(self):
        cut = diarize.automatic_distance([0.04, 0.06, 0.12])
        self.assertGreater(cut, 0.12)  # everything is merged

    def test_two_clearly_different_people_are_kept_apart(self):
        cut = diarize.automatic_distance([0.05, 0.09, 0.19, 0.59])
        self.assertTrue(0.19 < cut < 0.59)

    def test_nothing_far_apart_is_ever_merged_and_nothing_very_close_is_split(self):
        self.assertLessEqual(diarize.automatic_distance([0.1, 0.12, 0.6, 0.7]), 0.4)  # (the cut sits in the gap, not above 0.4 anyway)
        cut = diarize.automatic_distance([0.05, 0.06, 0.07, 0.08, 0.5])
        self.assertGreater(cut, 0.08)  # the close ones merge

    def test_no_clear_gap_uses_the_fixed_distance(self):
        ramp = [0.10, 0.14, 0.18, 0.22, 0.26, 0.30, 0.34, 0.38]
        self.assertEqual(diarize.automatic_distance(ramp), diarize.MERGE_DISTANCE)

    def test_two_people_whose_voices_merge_at_0_32_stay_two_people(self):
        # the shape of a real two-hour meeting: two people join at 0.32 to 0.34, a fixed 0.35 would have made them one speaker
        prints = {0: voice(0, 50), 1: voice(15, 40), 2: voice(47, 50), 3: voice(62, 40), 4: voice(170, 60), 5: voice(180, 40)}
        steps = [round(d, 2) for _i, _j, d, _t in diarize._merge_steps(prints)]
        self.assertIn(0.32, steps)
        self.assertEqual(len(set(diarize.merge_groups(prints, 0, 0.35).values())), 2)  # what a fixed distance did
        mapping = diarize.merge_groups(prints)
        self.assertEqual(len(set(mapping.values())), 3)
        self.assertEqual((mapping[0], mapping[2]) == (mapping[1], mapping[3]), True)
        self.assertNotEqual(mapping[0], mapping[2])


class MergingGroups(unittest.TestCase):
    def prints(self):
        return {0: voice(0, 50), 1: voice(10, 40), 2: voice(95, 60), 3: voice(103, 35), 4: voice(200, 45)}

    def test_the_groups_of_each_person_come_together_and_the_one_who_talks_most_is_speaker_0(self):
        m = diarize.merge_groups(self.prints())
        self.assertEqual(m[0], m[1])
        self.assertEqual(m[2], m[3])
        self.assertEqual(len({m[0], m[2], m[4]}), 3)
        self.assertEqual(m[2], 0)  # 95 seconds: the most

    def test_a_group_with_almost_no_speech_joins_the_nearest_person(self):
        p = self.prints()
        p[5] = voice(97, 1.0)  # a second of speech
        m = diarize.merge_groups(p)
        self.assertEqual(m[5], m[2])

    def test_a_number_of_people_you_set_is_used_instead(self):
        m = diarize.merge_groups(self.prints(), speakers=2)
        self.assertEqual(len(set(m.values())), 2)
        m = diarize.merge_groups(self.prints(), speakers=5)
        self.assertEqual(len(set(m.values())), 5)

    def test_a_fixed_distance_can_still_be_given(self):
        self.assertEqual(len(set(diarize.merge_groups(self.prints(), 0, 0.005).values())), 5)
        self.assertEqual(len(set(diarize.merge_groups(self.prints(), 0, 2.0).values())), 1)

    def test_a_single_group_is_one_speaker(self):
        self.assertEqual(diarize.merge_groups({7: voice(0, 10)}), {7: 0})


if __name__ == "__main__":
    unittest.main()
