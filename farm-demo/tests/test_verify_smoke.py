"""Negative controls for the CI evidence validator; fixtures are not measured results."""
import json
import tempfile
import unittest
from pathlib import Path
from verify_smoke import verify

class SmokeVerifierTest(unittest.TestCase):
    def fixture(self):
        r = dict(device_id="FARM_WS_01", sequence_number=1, period_s=300,
                 ts_s=1795453500, valid_mask=127, status=0, rainfall=0.2794,
                 wind_gust=12.0, wind_speed=4.0, temperature=25,
                 humidity=70, pressure=1000, battery_voltage=3.2, wind_direction=135)
        return "READY FARM_WS_01 SIMULATION\nWIND_ALARM_ON\nRAIN_TIPS=1\nWIND_ALARM_OFF\nTELEMETRY " + json.dumps(r) + "\n"

    def test_accept_valid_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "serial.log"
            log.write_text(self.fixture(), encoding="utf-8")
            self.assertEqual(verify(log, str(Path(tmp) / "test.sqlite"))["result"], "PASS")

    def test_reject_missing_event_or_wrong_data(self):
        cases = (self.fixture().replace("WIND_ALARM_OFF\n", ""),
                 self.fixture().replace("0.2794", "0.0"),
                 self.fixture().replace("1795453500", "1795453501"),
                 self.fixture().split("TELEMETRY")[0])
        for data in cases:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp:
                log = Path(tmp) / "serial.log"
                log.write_text(data, encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify(log, str(Path(tmp) / "test.sqlite"))
