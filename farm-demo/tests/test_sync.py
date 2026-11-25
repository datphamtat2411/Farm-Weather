import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"edge"))
from outbox import connect, store
from sync import Synchronizer, CloudUnavailable, delay_ms, values, compare

BASE=1795453200000
class Clock:
    def __init__(self): self.now=BASE
    def __call__(self): return self.now
    def advance(self,seconds): self.now+=seconds*1000

class Cloud:
    def __init__(self):
        self.available=False
        self.persist=True
        self.fail_after_write=False
        self.history={}
        self.packets=[]
    def connect(self):
        if not self.available: raise CloudUnavailable("offline")
    def close(self): pass
    def read(self,packet):
        if not self.available: raise CloudUnavailable("offline")
        return self.history
    def publish(self,packet):
        self.packets.append(packet)
        if self.persist:
            for dev,rs in packet.items():
                for r in rs:
                    self.history.setdefault((dev,r["ts"]),{}).update(
                        {k:v for k,v in r["values"].items() if k!="sync_mode"})
        if self.fail_after_write: raise CloudUnavailable("lost response")

def record(seq,ts,kind="periodic",device="FARM_WS_01"):
    base=dict(device_id=device,sequence_number=seq,timestamp=ts,status=0)
    if kind=="event":
        return dict(base,kind=kind,rain_10min_mm=5.0292,requested_period_s=300,buzzer=True)
    return dict(base,period_s=300,rainfall=0.2794,valid_mask=32)

class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"edge.sqlite"
        self.db=connect(self.path)
        self.clock=Clock()
        self.cloud=Cloud()
        self.worker=Synchronizer(self.db,self.cloud,clock=self.clock,jitter=lambda a,b:0)
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def insert(self,r):
        return store(self.db,r,{"received_at":self.clock()})
    def step(self,seconds=0):
        self.clock.advance(seconds)
        return self.worker.step()
    def count(self,state):
        return self.db.execute("SELECT count(*) FROM weather_outbox WHERE state=?",(state,)).fetchone()[0]
    def test_d04(self):
        for i in range(1,73):
            self.clock.advance(300)
            self.insert(record(i,self.clock()))
            self.step()
        self.assertEqual(self.count("PENDING"),72)
        self.assertEqual(self.worker.state,"OFFLINE")
        self.cloud.available=True
        self.step(60)
        self.assertEqual(self.count("SYNCED"),50)
        self.assertEqual(self.worker.state,"RECOVERING")
        self.step(1)
        self.assertEqual(self.count("SYNCED"),72)
        self.assertEqual(self.worker.state,"ONLINE")
        self.assertEqual(len(self.cloud.packets),2)
        self.assertEqual(sum(len(x["FARM_WS_01"]) for x in self.cloud.packets),72)
        self.assertEqual(sum(x["sync_mode"]=="live" for p in self.cloud.packets for rs in p.values() for x in [r["values"] for r in rs]),1)
        self.assertEqual(len(self.cloud.history),72)
        self.assertAlmostEqual(sum(v["rainfall"] for v in self.cloud.history.values()),20.1168)
        self.assertEqual(self.insert(record(1,BASE+300000)),"duplicate")
    def test_puback_not_verification_and_partial(self):
        self.cloud.available=True
        self.cloud.persist=False
        r=record(1,BASE)
        self.insert(r)
        self.step()
        self.assertEqual(self.count("PENDING"),1)
        self.assertEqual(self.count("SYNCED"),0)
        self.cloud.history[("FARM_WS_01",BASE)]={"record_id":"FARM_WS_01:1"}
        self.cloud.persist=True
        self.step(5)
        self.assertEqual(self.count("SYNCED"),1)
        self.assertEqual(len(self.cloud.packets),2)
    def test_lost_response_restart_and_events(self):
        self.cloud.available=True
        self.cloud.fail_after_write=True
        self.insert(record(1,BASE))
        self.insert(record(2,BASE,"event"))
        self.step()
        self.assertEqual(self.count("PENDING"),2)
        self.db.close()
        self.db=connect(self.path)
        self.worker=Synchronizer(self.db,self.cloud,clock=self.clock,jitter=lambda a,b:0)
        self.cloud.fail_after_write=False
        self.step(5)
        self.assertEqual(self.count("SYNCED"),2)
        self.assertEqual(len(self.cloud.packets),1)
        row=self.cloud.history[("FARM_WS_01",BASE)]
        self.assertEqual(row["rainfall"],0.2794)
        self.assertEqual(row["event_rain_10min_mm"],5.0292)
        self.assertEqual(row["event_record_id"],"FARM_WS_01:2")
    def test_conflicts_and_recovery(self):
        self.cloud.available=True
        self.insert(record(1,BASE))
        self.insert(record(2,BASE))
        self.insert(record(3,BASE+300000))
        self.cloud.history[("FARM_WS_01",BASE)]={"record_id":"other:1"}
        self.step(300)
        self.assertEqual(self.count("QUARANTINE"),2)
        self.assertEqual(self.count("SYNCED"),1)
        self.assertEqual(self.cloud.history[("FARM_WS_01",BASE)],{"record_id":"other:1"})
    def test_inflight_and_retention(self):
        self.insert(record(1,BASE))
        self.db.execute("UPDATE weather_outbox SET state='IN_FLIGHT'")
        self.db.commit()
        other=connect(self.path,recover=False)
        self.assertEqual(other.execute("SELECT state FROM weather_outbox").fetchone()[0],"IN_FLIGHT")
        other.close()
        self.db.close()
        self.db=connect(self.path)
        self.worker=Synchronizer(self.db,self.cloud,clock=self.clock,jitter=lambda a,b:0)
        self.cloud.available=True
        self.step()
        self.assertEqual(self.count("SYNCED"),1)
        self.step(86400)
        self.assertEqual(self.db.execute("SELECT payload_json FROM weather_outbox").fetchone()[0],"")
        self.assertEqual(self.insert(record(1,BASE)),"duplicate")
        self.assertEqual(self.insert(dict(record(1,BASE),rainfall=5)),"conflict")
        self.assertEqual([delay_ms(i,lambda a,b:0) for i in range(1,8)],[5000,10000,20000,40000,60000,60000,60000])
    def test_verifier_types(self):
        self.assertEqual(compare({"record_id":"a","rainfall":0.2794},{"record_id":"a","rainfall":"0.2794"}),"verified")
        self.assertEqual(compare({"record_id":"a","rainfall":0.2794},{"record_id":"a","rainfall":"0"}),"conflict")
        self.assertEqual(compare({"record_id":"a"},{"record_id":"b"}),"conflict")
