import csv
import io
import sys
import unittest
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"cloud"),str(ROOT/"edge")]
from history import records, report, csv_report, fetch_points, DAY, HOUR
from overview import VN

class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.start=int(datetime(2026,11,25,tzinfo=VN).timestamp()*1000)
        self.points={}

    def add(self, minutes, seq, period=300, mask=127, **kw):
        values=dict(record_id=f"FARM_WS_01:{seq}",sequence_number=seq,period_s=period,
                    valid_mask=mask,status=0,temperature=20,humidity=60,pressure=1000,
                    wind_speed=2,wind_gust=4,wind_direction=359,rainfall=0.2794,battery_voltage=3.2)
        values.update(kw)
        for k,v in values.items():
            self.points.setdefault(k,[]).append({"ts":self.start+minutes*60000,"value":v})

    def test_mixed_period_backfill_invalid_and_zero(self):
        self.add(20,3,900,wind_speed=10,wind_gust=18,wind_direction=1,rainfall=2.5146,temperature=30)
        self.add(5,1,rainfall=0)
        self.add(5,1,rainfall=0) # exact retry
        self.add(35,4,mask=126,temperature=99,rainfall=0.2794)
        self.points["event_record_id"]=[{"ts":self.start+600000,"value":"FARM_WS_01:2"}]
        rows=records(self.points,self.start,self.start+HOUR)
        self.assertEqual(len(rows),3)
        self.assertEqual([r["sequence_number"] for r in rows],[1,3,4])
        self.assertIsNone(rows[-1]["values"]["temperature"])
        s=report(rows,self.start,self.start+HOUR)["summary"]
        self.assertEqual(s["coverage_s"],1500)
        self.assertEqual(s["valid_coverage_s"],1200)
        self.assertEqual(s["metrics"]["temperature"]["avg"],25)
        self.assertAlmostEqual(s["metrics"]["wind_speed"]["avg"],6.8)
        self.assertEqual(s["metrics"]["wind_gust"]["max"],18)
        self.assertAlmostEqual(s["metrics"]["rainfall"]["total"],2.794)
        self.assertTrue(s["metrics"]["wind_direction"]["vector_deg"]<2)
        self.assertEqual(s["gaps"],[{"startTs":self.start+20*60000,"endTs":self.start+30*60000},
                                    {"startTs":self.start+35*60000,"endTs":self.start+HOUR}])
        exported=list(csv.DictReader(io.StringIO(csv_report(rows))))
        self.assertEqual(exported[0]["rainfall"],"0.0")
        self.assertEqual(exported[-1]["temperature"],"")
        self.assertTrue(exported[0]["timestamp_utc_plus_7"].endswith("+07:00"))

    def test_boundaries_overlap_empty(self):
        self.add(2,1,rainfall=99) # crosses start
        self.add(10,2,rainfall=2)
        self.add(13,3,rainfall=99) # overlaps prior interval
        self.add(65,4,900,rainfall=3) # crosses hour
        end=self.start+2*HOUR
        r=report(records(self.points,self.start,end),self.start,end)
        self.assertEqual(r["summary"]["boundary_periods"],1)
        self.assertEqual(r["summary"]["overlapping_periods"],1)
        self.assertEqual(r["summary"]["metrics"]["rainfall"]["total"],5)
        self.assertEqual(r["buckets"][0]["metrics"]["rainfall"]["total"],2)
        self.assertEqual(r["buckets"][1]["boundary_periods"],1)
        self.assertIsNone(r["buckets"][1]["metrics"]["rainfall"]["total"])
        e=report([],self.start,end)
        self.assertEqual(e["summary"]["coverage_pct"],0)
        self.assertIsNone(e["summary"]["metrics"]["rainfall"]["total"])

    def test_conflicting_record_fails(self):
        self.add(5,1)
        self.add(10,1)
        with self.assertRaises(ValueError):
            records(self.points,self.start,self.start+HOUR)

    def test_month_is_chunked_and_local_day_aligned(self):
        calls=[]
        outer=self
        class Api:
            def request(self,method,path,**kw):
                calls.append(kw)
                return {"record_id":[{"ts":kw["endTs"]-1,"value":"FARM_WS_01:1"}]}
        result=fetch_points(Api(),"station",self.start,self.start+30*DAY)
        self.assertEqual(len(calls),30)
        self.assertEqual(len(result["record_id"]),30)
        r=report([],self.start+HOUR,self.start+7*DAY)
        self.assertEqual(r["bucket_s"],86400)
        self.assertEqual(r["buckets"][0]["endTs"],self.start+DAY)
        self.assertEqual(len(r["buckets"]),7)
