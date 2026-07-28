import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
import sys

from kobo.agy_image import AgyImageAdapter, AgyImageInvalid


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000" 
    "1f15c4890000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
)


class AgyImageTests(unittest.TestCase):
    def test_generate_validates_png_and_command(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "image.png"
            seen = {}

            def runner(command, **kwargs):
                seen["command"] = command
                output.write_bytes(PNG_1X1)
                return CompletedProcess(command, 0, "saved", "")

            adapter = AgyImageAdapter(sys.executable, runner=runner)
            result = adapter.generate("save exactly to " + str(output), output)
            self.assertEqual(result["format"], "png")
            self.assertEqual(result["width"], 1)
            self.assertIn("--print", seen["command"])
            self.assertIn("--dangerously-skip-permissions", seen["command"])

    def test_rejects_non_image_output(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "image.png"
            output.write_text("not an image", encoding="ascii")
            adapter = AgyImageAdapter(sys.executable, runner=lambda *a, **k: CompletedProcess([], 0))
            with self.assertRaises(AgyImageInvalid):
                adapter.generate("prompt", output)


if __name__ == "__main__":
    unittest.main()
