"""Artifact layout regression; synthetic fixtures only, never acceptance evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from verify_task17_replay import select_artifact

class ReplayArtifactTest(unittest.TestCase):
    def fixture(self,folder,sha="candidate",status="PASS"):
        folder.mkdir(parents=True,exist_ok=True)
        (folder/"commit.txt").write_text(sha)
        for filename in ("link-recovery.json","link-recovery-wire.json"):
            (folder/filename).write_text(json.dumps({"result":status}))
        (folder/"link-recovery.log").write_text("fixture")

    def test_flat_single_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.fixture(root)
            self.assertEqual(select_artifact(root,"candidate"),root/"link-recovery.log")

    def test_multiple_matches_choose_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.fixture(root/"simulation-link-recovery-attempt-1","old")
            latest=root/"simulation-link-recovery-attempt-2"
            self.fixture(latest)
            self.assertEqual(select_artifact(root,"candidate"),latest/"link-recovery.log")

    def test_reject_wrong_sha_failed_report_and_ambiguous_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.fixture(root,"other")
            with self.assertRaisesRegex(AssertionError,"wrong simulation commit"):
                select_artifact(root,"candidate")
            self.fixture(root,status="FAIL")
            with self.assertRaisesRegex(AssertionError,"link verifier did not pass"):
                select_artifact(root,"candidate")
            self.fixture(root)
            self.fixture(root/"simulation-link-recovery-attempt-1")
            with self.assertRaisesRegex(AssertionError,"ambiguous"):
                select_artifact(root,"candidate")

if __name__=="__main__":
    unittest.main()
