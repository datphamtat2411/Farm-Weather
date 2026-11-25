import json
import unittest
from verify_protocol import decode, WeatherRecord, WeatherEvent, serial

class ProtocolTest(unittest.TestCase):
    def record(self):
        return WeatherRecord(sequence_number=1,ts_s=1795453200,period_s=300,status=0,valid_mask=32,rain_tips=0)

    def test_zero_and_identity(self):
        msg=self.record()
        self.assertEqual(decode(10,msg.SerializeToString(),"FARM_WS_02")["rainfall"],0)
        self.assertEqual(decode(10,msg.SerializeToString(),"FARM_WS_02")["device_id"],"FARM_WS_02")

    def test_reject_missing_and_invalid(self):
        for change in (
            lambda m: m.ClearField("rain_tips"), lambda m: setattr(m,"temp_x10",10),
            lambda m: setattr(m,"period_s",301), lambda m: setattr(m,"valid_mask",128),
            lambda m: setattr(m,"status",32), lambda m: setattr(m,"ts_s",0),
            lambda m: setattr(m,"sequence_number",0), lambda m: m.ClearField("ts_s"),
        ):
            msg=self.record();change(msg)
            with self.assertRaises(ValueError):
                decode(10,msg.SerializePartialToString(),"FARM_WS_01")

    def test_reject_wire_errors(self):
        p=self.record().SerializeToString()
        for port,payload,device in ((10,b"", "x"),(10,p[:-1],"x"),(10,p+b"\x80","x"),
                                    (10,p+b"\x78\x01","x"),(10,b"x"*65,"x"),
                                    (20,p,"x"),(11,p,"x"),(10,p,"")):
            with self.assertRaises(ValueError):
                decode(port,payload,device)

    def test_event_no_periodic_rain(self):
        msg=WeatherEvent(sequence_number=2,ts_s=1795453200,changed_mask=2,status=2,
                         requested_period_s=300,fault_mask=0,changed_fault_mask=0,
                         mode=0,buzzer=False,stop_request=False,rain_10min_tips=18)
        row=decode(11,msg.SerializeToString(),"FARM_WS_01")
        self.assertNotIn("rainfall",row)
        self.assertEqual(row["rain_10min_mm"],5.0292)

    def test_serial_missing_wire_fails(self):
        with self.assertRaises(ValueError):
            serial('TELEMETRY {"device_id":"FARM_WS_01","sequence_number":1}')

    def test_serial_reference_and_mismatch(self):
        msg=self.record()
        row=dict(device_id="FARM_WS_01",sequence_number=1,ts_s=1795453200,
                 period_s=300,status=0,valid_mask=32,rainfall=0,
                 fport=10,wire_hex=msg.SerializeToString().hex())
        self.assertEqual(serial("TELEMETRY "+json.dumps(row))["records"][0]["record"]["rainfall"],0)
        for patch in (dict(rainfall=0.2794),dict(temperature=0),dict(fport=11),
                      dict(ts_s=1795453201),dict(wire_hex="ff"),dict(sequence_number=2)):
            with self.assertRaises(ValueError):
                serial("TELEMETRY "+json.dumps(dict(row,**patch)))
