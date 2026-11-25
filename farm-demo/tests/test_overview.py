import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"cloud"),str(ROOT/"edge")]
from overview import readings,rain_and_trend
from outbox import connect,store,radio_diagnostics

class OverviewTest(unittest.TestCase):
    def test_invalid_and_mixed_timestamp(self):
        ts=1000
        p=lambda t,v:[{"ts":t,"value":v}]
        data={"record_id":p(ts,"FARM_WS_01:1"),"valid_mask":p(ts,"126"),
              "temperature":p(ts-100,"29.5"),"humidity":p(ts,"0"),
              "pressure":p(ts-100,"1000"),"wind_direction":p(ts,"0")}
        result=readings(data,ts)
        self.assertIsNone(result["temperature"])
        self.assertIsNone(result["pressure"])
        self.assertEqual(result["humidity"],0)
        self.assertEqual(result["wind_direction"],0)
        self.assertIsNone(readings(data,None)["humidity"])

    def test_day_rain_and_gap(self):
        day=int(datetime(2026,11,25,tzinfo=timezone(timedelta(hours=7))).timestamp()*1000)
        data={}
        def record(t,seq,period,mask,rain,temp):
            for k,v in dict(record_id=f"FARM_WS_01:{seq}",period_s=period,valid_mask=mask,
                            rainfall=rain,temperature=temp).items():
                data.setdefault(k,[]).append({"ts":day+t*1000,"value":v})
        record(120,1,300,127,8,24) # crosses midnight
        record(420,2,300,127,0,25)
        record(720,3,300,126,0.2794,99) # temperature invalid
        record(1020,3,300,127,0.2794,26) # duplicate record ID
        record(1920,4,900,95,99,27) # rain invalid
        record(2820,5,900,127,2.5146,28)
        rain,trend=rain_and_trend(data,day-86400000,day+3000*1000)
        self.assertAlmostEqual(rain["total"],2.794)
        self.assertEqual(rain["coverage_s"],1500)
        self.assertEqual(len(trend),5)
        self.assertIsNone(trend[2]["value"])

    def test_radio_unknown_and_simulation_provenance(self):
        db=connect(":memory:")
        store(db,{"device_id":"S","sequence_number":1,"timestamp":10},
              {"received_at":20,"receivers":[{"rssi":-80,"snr":4},{"rssi":-60,"snr":6}],"simulated":True})
        self.assertEqual(radio_diagnostics(db)["S"],{"rssi":-60,"snr":6,"received_at":20,"simulated":True})
        store(db,{"device_id":"S","sequence_number":2,"timestamp":30},{"received_at":40,"receivers":[]})
        self.assertIsNone(radio_diagnostics(db)["S"]["rssi"])
        db.close()
