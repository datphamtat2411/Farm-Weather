import unittest
from energy_budget import budget,report
class EnergyTest(unittest.TestCase):
    def test_report_values(self):
        a=budget()
        self.assertAlmostEqual(sum(s["ma_s"] for s in a["states"]),562.9)
        self.assertAlmostEqual(a["average_ma"],562.9/300)
        self.assertAlmostEqual(a["battery_wh_day"],0.286,places=3)
        self.assertAlmostEqual(a["minimum_ah"],a["battery_wh_day"]*3/2.048,places=10)
        self.assertAlmostEqual(round(a["battery_wh_day"],3)*3/2.048,0.419,places=3)
        self.assertAlmostEqual(a["design_ah"],0.63,places=2)
        self.assertGreater(a["selected_ah"],a["design_ah"])
        self.assertAlmostEqual(report()["gateway_minimum_ah"],20.833333333)
    def test_low_power_still_measures(self):
        a,b=budget(),budget(900)
        self.assertEqual(sum(s["seconds"] for s in b["states"]),900)
        self.assertEqual(b["states"][0]["seconds"],3*a["states"][0]["seconds"])
        self.assertLess(b["average_ma"],a["average_ma"])
        with self.assertRaises(ValueError):budget(600)
