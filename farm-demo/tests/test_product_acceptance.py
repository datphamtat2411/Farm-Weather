"""Negative controls for the Task 17 gate, not acceptance evidence."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

CI=Path(__file__).resolve().parents[1]/"ci"
sys.path.insert(0,str(CI))
from product_acceptance import validate, report

class ProductGateTest(unittest.TestCase):
    def test_missing_artifacts_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(ValueError,"missing native-evidence"):
                validate(root,"candidate",root)
            self.assertFalse((root/"acceptance.json").exists())

    def test_wrong_sha_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/"native-evidence"
            folder.mkdir()
            (folder/"commit.txt").write_text("obsolete")
            with self.assertRaisesRegex(ValueError,"wrong commit"):
                validate(root,"candidate",root)
            self.assertFalse((root/"acceptance.json").exists())

    def test_skipped_failed_or_missing_report_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            with self.assertRaisesRegex(ValueError,"missing"):
                report(folder,"report.json")
            for result in ("SKIPPED","FAIL","DEFERRED",None):
                (folder/"report.json").write_text(json.dumps({"result":result}))
                with self.assertRaisesRegex(ValueError,"not PASS"):
                    report(folder,"report.json")

if __name__=="__main__":
    unittest.main()
