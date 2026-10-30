import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt.transcription import models


class EnsureModel(unittest.TestCase):
    def test_a_local_folder_with_weights_passes(self):
        d = Path(tempfile.mkdtemp())
        (d / "weights.npz").write_bytes(b"x")
        models.ensure_model(str(d))

    def test_a_local_folder_without_weights_or_with_empty_ones_is_refused(self):
        d = Path(tempfile.mkdtemp())
        with self.assertRaises(RuntimeError):
            models.ensure_model(str(d))
        (d / "weights.safetensors").write_bytes(b"")
        with self.assertRaises(RuntimeError):
            models.ensure_model(str(d))


if __name__ == "__main__":
    unittest.main()
