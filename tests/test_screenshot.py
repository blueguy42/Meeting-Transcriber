import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt import screenshot as S

SCREEN = (840.0, 540.0)


class Geometry(unittest.TestCase):
    def test_buttons_sit_inside_the_frame_at_the_bottom_right(self):
        cancel, add, where = S.button_rects((100, 80, 420, 260), SCREEN)
        self.assertEqual(where, "inside")
        self.assertLess(cancel[0], add[0])  # cancel (✕) left of add (✓)
        self.assertEqual(add[0] + add[2], 100 + 420 - S.INSET)  # 8 points from the right edge
        self.assertEqual(add[1] + add[3], 80 + 260 - S.INSET)  # and from the bottom

    def test_small_area_puts_them_outside_and_they_stay_on_screen(self):
        _c, _a, where = S.button_rects((100, 80, 70, 40), SCREEN)
        self.assertEqual(where, "below")
        _c, add, where = S.button_rects((700, 480, 70, 40), SCREEN)  # no room below
        self.assertEqual(where, "above")
        c, add, _w = S.button_rects((0, 0, 840, 540), SCREEN)  # the whole screen
        for r in (c, add):
            self.assertTrue(0 <= r[0] and r[0] + r[2] <= SCREEN[0] and 0 <= r[1] and r[1] + r[3] <= SCREEN[1])

    def test_normalized_corners_and_clamping(self):
        self.assertEqual(S.normalized(300, 200, 100, 50, SCREEN), (100, 50, 200, 150))  # dragged up and to the left
        self.assertEqual(S.normalized(-20, -5, 900, 600, SCREEN), (0, 0, 840, 540))  # kept on the screen

    def test_points_to_pixels_on_a_retina_screen(self):
        self.assertEqual(S.to_pixels((100, 80, 420, 260), SCREEN, (1680, 1080)), (200, 160, 840, 520))
        self.assertEqual(S.to_pixels((0, 0, 840, 540), SCREEN, (1680, 1080)), (0, 0, 1680, 1080))
        x, y, w, h = S.to_pixels((839.6, 539.6, 0.2, 0.2), SCREEN, (1680, 1080))  # never empty, never past the picture
        self.assertTrue(w >= 1 and h >= 1 and x + w <= 1680 and y + h <= 1080)


if __name__ == "__main__":
    unittest.main()
