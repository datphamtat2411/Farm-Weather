"""Negative controls for the CI evidence validator; fixtures are not measured results."""
import json
import tempfile
import unittest
from pathlib import Path
from verify_smoke import verify

class SmokeVerifierTest(unittest.TestCase):
    def fixture(self):
        r = dict(device_id="FARM_WS_01", sequence_number=3, period_s=300,
                 ts_s=1795453500, valid_mask=127, status=0, rainfall=0.2794,
                 wind_gust=12.0, wind_speed=4.0, temperature=25,
                 humidity=70, pressure=1000, battery_voltage=3.2, wind_direction=135)
        on = dict(device_id="FARM_WS_01",kind="event",sequence_number=1,ts_s=1795453205,changed_mask=1,status=1,mode="ALARM",buzzer=True,stop_request=True,wind_window_mps=10)
        off = dict(on,sequence_number=2,ts_s=1795453268,status=0,mode="AWAIT_INSPECTION",buzzer=False,stop_request=False,wind_window_mps=6)
        return "BOOT FARM_WS_01\nBME280_I2C_READY\nREADY FARM_WS_01 SIMULATION\nEVENT "+json.dumps(on)+"\nWIND_ALARM_ON\nRAIN_TIPS=1\nEVENT "+json.dumps(off)+"\nWIND_ALARM_OFF\nTELEMETRY " + json.dumps(r) + "\n"

    def test_accept_valid_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "serial.log"
            log.write_text(self.fixture(), encoding="utf-8")
            self.assertEqual(verify(log, str(Path(tmp) / "test.sqlite"))["result"], "PASS")

    def test_accept_coalesced_wokwi_serial_stream(self):
        # Observed CLI artifact: all control markers are concatenated and
        # the final complete telemetry object ends with CRLF.
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "serial.log"
            log.write_text(self.fixture().replace("\n", "").strip() + "\r\n", encoding="utf-8")
            self.assertEqual(verify(log, str(Path(tmp) / "test.sqlite"))["result"], "PASS")

    def test_accept_current_radio_diagnostics(self):
        trace=self.fixture().replace("BOOT FARM_WS_01","FLASH_READY highwater=0 pending=0 corrupt=0\nBOOT FARM_WS_01").replace(
            "READY FARM_WS_01 SIMULATION","SIM_RTC offset=0 trusted=1\nREADY FARM_WS_01 SIMULATION").replace(
            "WIND_ALARM_ON","BUFFER_STORED 1 pending=1 used=1\nWIND_ALARM_ON\nRADIO_TX seq=1 fcnt=1 fport=11 hex=0801 delivered=1 ts=5\nRX1\nSIM_GATEWAY_ACK 1\nACK_FRAME ts=6 window=1 fport=20 hex=0801\nDOWNLINK fport=20 rx=accepted count=1 pending=0")
        for text in (trace,trace.replace("\n","")):
            with tempfile.TemporaryDirectory() as tmp:
                log=Path(tmp)/"serial.log"
                log.write_text(text)
                self.assertEqual(verify(log,str(Path(tmp)/"test.sqlite"))["result"],"PASS")
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/"serial.log"
            log.write_text(trace+"HARDWARE_ERROR FLASH_ACK")
            with self.assertRaises(ValueError):
                verify(log,str(Path(tmp)/"test.sqlite"))

    def test_reject_missing_event_or_wrong_data(self):
        cases = (self.fixture().replace("WIND_ALARM_OFF\n", ""),
                 self.fixture().replace("0.2794", "0.0"),
                 self.fixture().replace("RAIN_TIPS=1", "RAIN_TIPS=10"),
                 self.fixture().replace("RAIN_TIPS=1", "RAIN_TIPS=10").replace("\n", ""),
                 self.fixture().replace("WIND_ALARM_OFF", "WIND_ALARM_OFFEXTRA"),
                 self.fixture().replace("WIND_ALARM_ON", "WIND_ALARM_ON\nWIND_ALARM_ON"),
                 self.fixture().replace('"sequence_number": 1', '"sequence_number": true'),
                 self.fixture().replace("1795453500", "1795453501"),
                 self.fixture().split("TELEMETRY")[0])
        for data in cases:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp:
                log = Path(tmp) / "serial.log"
                log.write_text(data, encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify(log, str(Path(tmp) / "test.sqlite"))
