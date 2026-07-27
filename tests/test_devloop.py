import json
import tempfile
import unittest
from pathlib import Path

from kobo.devloop import DevLoop,DevLoopConfig

class DevLoopTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); (self.root/"instructions").mkdir(); self.cfg=self.root/"devloop.json"; self.cfg.write_text(json.dumps({"instructions":"instructions","database":"state.db","implement":["agent","{instruction_path}","{result_path}"],"review":[],"tests":[]}),encoding="utf-8")
    def tearDown(self):self.temp.cleanup()
    def test_detects_unpaired_instruction_and_dry_run_is_safe(self):
        (self.root/"instructions/instruction-20260727-1.md").write_text("task",encoding="utf-8"); loop=DevLoop(DevLoopConfig.load(self.cfg)); result=loop.once(); self.assertEqual(result["status"],"planned"); self.assertIn("instruction-20260727-1.md",result["implement_command"][1])
    def test_result_pair_is_not_repeated(self):
        directory=self.root/"instructions"; (directory/"instruction-20260727-1.md").write_text("task",encoding="utf-8"); (directory/"result-20260727-1.md").write_text("done",encoding="utf-8"); self.assertEqual(DevLoop(DevLoopConfig.load(self.cfg)).once()["status"],"idle")
    def test_unknown_placeholder_is_rejected(self):
        data=json.loads(self.cfg.read_text()); data["implement"]=["agent","{secret}"]; self.cfg.write_text(json.dumps(data)); (self.root/"instructions/instruction-20260727-2.md").write_text("task");
        with self.assertRaises(Exception):DevLoop(DevLoopConfig.load(self.cfg)).once()

if __name__=="__main__":unittest.main()
