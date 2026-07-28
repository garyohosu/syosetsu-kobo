import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kobo.visual_publish import DEFAULTS, VisualPublisher


class VisualPlanTests(unittest.TestCase):
    def test_plan_has_required_body_interval(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = SimpleNamespace(root=root, store=root / ".kobo", visual_publish={})
            publisher = object.__new__(VisualPublisher)
            publisher.orchestrator = SimpleNamespace(config=config)
            publisher.settings = dict(DEFAULTS)
            publisher.dummy = True
            blocks = [f"段落{i}。" + ("本文" * 120) for i in range(40)]
            positions = publisher._plan_items(blocks)
            anchors = [x[1] for x in positions]
            gaps = [anchors[0]] + [b - a for a, b in zip(anchors, anchors[1:])] + [len("\n\n".join(blocks)) - anchors[-1]]
            self.assertGreaterEqual(len(positions), 3)
            self.assertLessEqual(max(gaps), publisher.settings["max_chars_without_image"])

    def test_rendered_body_round_trips(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = SimpleNamespace(root=root, store=root / ".kobo", visual_publish={})
            publisher = object.__new__(VisualPublisher)
            publisher.orchestrator = SimpleNamespace(config=config)
            publisher.settings = dict(DEFAULTS)
            publisher.dummy = True
            source = "# 第一話\n\n## 場面\n\n危険な\u0026調査。"
            html = publisher._render_html(source, [])
            self.assertIn("危険な&amp;調査。", html)
            self.assertEqual(publisher._normalized_source(source), publisher._normalized_html(html))


if __name__ == "__main__":
    unittest.main()
