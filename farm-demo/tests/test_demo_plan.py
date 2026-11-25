import importlib.util
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location("farm_demo_runner",Path(__file__).resolve().parents[1]/"demo/run.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class DemoPlanTest(unittest.TestCase):
    def test_controls_and_reset(self):
        plan={"name":"control","segments":[{"seconds":2,"set":{"lte":False,"rain_tips":1,"confirm":True}},{"seconds":1,"set":{}}]}
        _,parts=module.load_plan(plan)
        self.assertEqual(parts[0][1]["rain_tips"],1)
        self.assertEqual(parts[1][1]["rain_tips"],0)
        self.assertFalse(parts[1][1]["confirm"])
        self.assertFalse(parts[1][1]["lte"])
        self.assertTrue(module.load_plan({"name":"new","segments":[{"seconds":1,"set":{}}]})[1][0][1]["lte"])
    def test_invalid(self):
        for controls in ({"wind_hz":True},{"temperature":float("nan")},{"direction":7},{"lte":0},{"pump":True},{"rain_tips":11}):
            with self.subTest(controls=controls),self.assertRaises(ValueError):
                module.load_plan({"name":"invalid","segments":[{"seconds":1,"set":controls}]})
        with self.assertRaises(ValueError):
            module.load_plan({"name":"long","segments":[{"seconds":43201,"set":{}}]})
