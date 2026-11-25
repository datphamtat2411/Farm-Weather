import base64
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"edge"))
from gateway import handle
from outbox import connect, CapacityError, diagnostics
from weather_pb2 import WeatherRecord, WeatherEvent, WeatherAck

APP, DEV = "farm-demo", "0000000000000001"
TOPIC = f"application/{APP}/device/{DEV}/event/up"
MAPPING = {f"{APP}/device/{DEV}":"FARM_WS_01"}
def report(seq=1052):
    return WeatherRecord(sequence_number=seq,ts_s=1795453200,period_s=300,
        rain_tips=9,status=0,valid_mask=32)
def event(seq=1053):
    return WeatherEvent(sequence_number=seq,ts_s=1795453200,changed_mask=1,status=1,
        requested_period_s=300,fault_mask=0,changed_fault_mask=0,mode=1,buzzer=True,
        stop_request=True,rain_10min_tips=18)
def uplink(msg, port=10, **changes):
    body=dict(deviceInfo=dict(applicationId=APP,devEui=DEV),fPort=port,fCnt=1,
              data=base64.b64encode(msg.SerializeToString()).decode(),
              time="2026-11-23T18:00:00Z",rxInfo=[dict(gatewayId=DEV,rssi=-90,snr=7.5)])
    # Choose reception later than the report's fixed timestamp.
    body["time"]="2026-11-26T00:00:00Z"
    body.update(changes)
    return json.dumps(body).encode()

class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"edge.sqlite"
        self.db=connect(self.path)
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def test_commit_restart_replay(self):
        raw=uplink(report())
        status, down=handle(self.db,MAPPING,TOPIC,raw)
        self.assertEqual(status,"inserted")
        self.assertEqual(down[0],f"application/{APP}/device/{DEV}/command/down")
        self.assertEqual(list(WeatherAck.FromString(base64.b64decode(down[1]["data"])).sequence_number),[1052])
        # Independent connection sees committed row before ACK can leave service.
        with sqlite3.connect(self.path) as reader:
            payload,meta,ts,state=reader.execute("SELECT payload_json,metadata_json,ts_ms,state FROM weather_outbox").fetchone()
            self.assertEqual((ts,state),(1795453200000,"PENDING"))
            self.assertEqual(json.loads(payload)["rainfall"],2.5146)
            self.assertNotIn("temperature",json.loads(payload))
            self.assertEqual(json.loads(meta)["receivers"][0]["rssi"],-90)
        self.db.execute("UPDATE weather_outbox SET state='IN_FLIGHT'")
        self.db.commit()
        self.db.close()
        self.db=connect(self.path)
        status, replay=handle(self.db,MAPPING,TOPIC,uplink(report(),fCnt=9))
        self.assertEqual((status,replay),("duplicate",down))
        self.assertEqual(self.db.execute("SELECT count(*),min(state) FROM weather_outbox").fetchone(),(1,"PENDING"))
        changed=report();changed.rain_tips=10
        self.assertEqual(handle(self.db,MAPPING,TOPIC,uplink(changed)),("conflict",None))
        self.assertEqual(self.db.execute("SELECT count(*) FROM quarantine").fetchone()[0],1)
    def test_event_and_invalid(self):
        self.assertEqual(handle(self.db,MAPPING,TOPIC,uplink(event(),11))[0],"inserted")
        kind,priority,payload=self.db.execute("SELECT kind,priority,payload_json FROM weather_outbox").fetchone()
        self.assertEqual((kind,priority),("event",1))
        self.assertNotIn("rainfall",json.loads(payload))
        cases=[uplink(report(),data="bad!"),uplink(report(),fPort=20),
               uplink(report(),timeTrusted=False),uplink(report(),time="2020-01-01T00:00:00Z"),
               uplink(report(),deviceInfo={"applicationId":APP,"devEui":"ffffffffffffffff"}),
               uplink(report(),fCnt=True)]
        for raw in cases:
            self.assertEqual(handle(self.db,MAPPING,TOPIC,raw),("invalid",None))
        self.assertEqual(handle(self.db,{},TOPIC,uplink(report())),("invalid",None))
        self.assertEqual(self.db.execute("SELECT count(*) FROM weather_outbox").fetchone()[0],1)
    def test_failed_write_no_ack(self):
        self.db.execute("""CREATE TRIGGER fail_insert BEFORE INSERT ON weather_outbox
            BEGIN SELECT RAISE(ABORT,'disk error'); END""")
        with self.assertRaises(sqlite3.Error):
            handle(self.db,MAPPING,TOPIC,uplink(report()))
        self.assertEqual(self.db.execute("SELECT count(*) FROM weather_outbox").fetchone()[0],0)
        self.db.execute("DROP TRIGGER fail_insert")
        with self.assertRaises(CapacityError):
            handle(self.db,MAPPING,TOPIC,uplink(report()),budget=1)
        self.assertEqual(diagnostics(self.db)["pending"],0)
        self.assertEqual(handle(self.db,MAPPING,TOPIC,uplink(report()))[0],"inserted")
    def test_out_of_order_and_same_second(self):
        for seq in (3,1,2):
            self.assertEqual(handle(self.db,MAPPING,TOPIC,uplink(report(seq)))[0],"inserted")
        self.assertEqual(handle(self.db,MAPPING,TOPIC,uplink(event(4),11))[0],"inserted")
        self.assertEqual(diagnostics(self.db)["pending"],4)

if __name__=="__main__":
    unittest.main()
