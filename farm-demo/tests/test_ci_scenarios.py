"""Offline tests for the scenario inventory, not simulated hardware evidence."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
from scenarios import load, validate, run, validate_chip_controls

class ScenarioContractTest(unittest.TestCase):
    def test_all_mandatory_scenarios(self):
        self.assertGreaterEqual(len(load()), 4)
    def test_invalid_chip_control_id(self):
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            chips=root/"firmware"/"chips"
            chips.mkdir(parents=True)
            (chips/"test.chip.json").write_text(json.dumps({"controls":[{"id":"invalid_control"}]}))
            with self.assertRaisesRegex(ValueError,"invalid chip control ID"):
                validate_chip_controls(root)
    def test_reject_removed_scenario(self):
        with self.assertRaises(ValueError):
            validate(load()[:-1])
    def test_reject_removed_verifier(self):
        entries = copy.deepcopy(load())
        next(e for e in entries if e["id"] == "weather")["checks"].pop()
        with self.assertRaises(ValueError):
            validate(entries)
    def test_reject_missing_verifier(self):
        entries = copy.deepcopy(load())
        entries[0]["checks"][0]["script"] = "verify_not_here.py"
        with self.assertRaises(ValueError):
            validate(entries)
    def test_reject_duplicate_id(self):
        entries = load()
        with self.assertRaises(ValueError):
            validate(entries + [entries[0]])
    def test_reject_unsafe_log_path(self):
        entries = copy.deepcopy(load())
        entries[0]["serial"] = "../outside.log"
        with self.assertRaises(ValueError):
            validate(entries)
    def test_missing_token_fails_before_simulation(self):
        with patch.dict("os.environ", {"WOKWI_CLI_TOKEN": ""}), patch("scenarios.logged") as logged:
            with self.assertRaises(ValueError):
                run(load()[0])
            logged.assert_not_called()
