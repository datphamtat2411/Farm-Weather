import base64
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"edge"))
import rpc
from outbox import connect
from serial_bridge import SerialBridge
from weather_pb2 import FarmCommand, CommandResult

class RpcTest(unittest.TestCase):
    def setUp(self):
        self.db=connect(":memory:")
        rpc.schema(self.db)
        self.id=str(uuid.uuid4())
        self.now=1795453200000
        self.params={"command_id":self.id,"created_at_ms":self.now,"expires_at_ms":self.now+900000}
        self.raw=json.dumps({"device":"FARM_WS_01","data":{"id":42,"method":"test_buzzer","params":self.params}})
        self.stations={"FARM_WS_01":"uuid"}
    def tearDown(self):
        self.db.close()
    def state(self):
        return self.db.execute("SELECT state FROM farm_commands WHERE command_id=?",(self.id,)).fetchone()[0]
    def down(self):
        return rpc.next_downlink(self.db,"FARM_WS_01","farm-demo","0000000000000001",7,now=self.now+6000)
    def test_lifecycle(self):
        rpc.admit(self.db,self.raw,self.stations,self.now)
        self.assertEqual(self.state(),"PENDING")
        topic,down=self.down()
        self.assertEqual(self.state(),"QUEUED")
        msg=FarmCommand.FromString(base64.b64decode(down["data"]))
        self.assertEqual(msg.ack_sequence,7)
        self.assertEqual(msg.command_id,uuid.UUID(self.id).bytes)
        bridge=SerialBridge("farm-demo","0000000000000001",lambda *a:None)
        bridge.active=7
        bridge.on_downlink(topic,json.dumps(down))
        self.assertEqual(bridge.on_line("RX1"),"DOWNLINK 21 "+base64.b64decode(down["data"]).hex())
        self.assertIsNone(bridge.on_line("RX2"))
        result=CommandResult(sequence_number=8,ts_s=self.now//1000+7,command_id=msg.command_id,
            outcome=1,reason=0,config_version=1,wind_alarm_on=10,wind_alarm_off=7,report_period_s=300,
            applied_period_s=300,outputs=1)
        decoded=rpc.decode_result(result.SerializeToString(deterministic=True))
        with self.assertRaises(ValueError):
            rpc.record_result(self.db,"WRONG",decoded,self.now+8000)
        self.assertEqual(rpc.record_result(self.db,"FARM_WS_01",decoded,self.now+8000),"inserted")
        self.assertEqual(rpc.record_result(self.db,"FARM_WS_01",decoded,self.now+8000),"duplicate")
        self.assertEqual(self.state(),"APPLIED")
        self.assertEqual([x[0] for x in self.db.execute("SELECT state FROM command_events ORDER BY revision")],
                         ["PENDING","QUEUED","APPLIED"])
    def test_expire_and_scope(self):
        with self.assertRaises(ValueError): rpc.admit(self.db,self.raw,{},self.now)
        rpc.admit(self.db,self.raw,self.stations,self.now)
        rpc.expire(self.db,self.now+900000)
        self.assertEqual(self.state(),"EXPIRED")
        self.assertIsNone(self.down())
    def test_timeout_after_dispatch(self):
        rpc.admit(self.db,self.raw,self.stations,self.now)
        self.down()
        rpc.expire(self.db,self.now+900000)
        self.assertEqual(self.state(),"UNKNOWN")
    def test_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"rpc.sqlite"
            with connect(path) as db:
                rpc.schema(db);rpc.admit(db,self.raw,self.stations,self.now)
            db.close()
            db=connect(path)
            self.assertEqual(db.execute("SELECT state FROM farm_commands").fetchone()[0],"PENDING")
            self.assertIsNotNone(rpc.next_downlink(db,"FARM_WS_01","farm-demo","0000000000000001",7,now=self.now+6000))
            db.close()

if __name__=="__main__": unittest.main()
