import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"edge"))
from outbox import connect, ingest

class OutboxTest(unittest.TestCase):
    def test_duplicate_conflict(self):
        db=connect(":memory:")
        record={"device_id":"FARM_WS_01","sequence_number":1,"ts_s":1795453200,"rainfall":0.2794}
        ingest(db,record)
        ingest(db,record)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM weather_outbox").fetchone()[0],1)
        with self.assertRaises(ValueError):
            ingest(db,dict(record,rainfall=9))
        self.assertEqual(db.execute("SELECT ts_ms,state FROM weather_outbox").fetchone(),(1795453200000,"PENDING"))
        db.close()

if __name__=="__main__":
    unittest.main()
