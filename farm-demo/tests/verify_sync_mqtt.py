"""D04: real MQTT QoS1 + independent HTTP history + durable SQLite test Cloud.
No ThingsBoard PE or physical 4G claim. Logical time avoids six hours of waiting.
"""
import argparse
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import paho.mqtt.client as mqtt

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"edge"))
from outbox import connect
from gateway import handle
from sync import Synchronizer
from cloud import MqttRestCloud
from test_gateway import report,uplink,MAPPING,TOPIC
from test_sync import Clock

def wait(predicate,timeout=10):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(.025)
    raise AssertionError("integration deadline exceeded")

def run(edge_path,cloud_path):
    assert not edge_path.exists() and not cloud_path.exists()
    with sqlite3.connect(cloud_path) as db:
        db.execute("CREATE TABLE history(device TEXT, ts INTEGER, key TEXT, value TEXT, PRIMARY KEY(device,ts,key))")
    packets=[]
    subscriptions=threading.Event()
    errors=[]
    class History(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed=urllib.parse.urlsplit(self.path)
            assert parsed.path=="/api/plugins/telemetry/DEVICE/test-device/values/timeseries"
            q=urllib.parse.parse_qs(parsed.query)
            assert q["agg"]==["NONE"]
            with sqlite3.connect(cloud_path) as db:
                rows=db.execute("SELECT ts,key,value FROM history WHERE device=? AND ts>=? AND ts<? ORDER BY ts",
                    ("FARM_WS_01",int(q["startTs"][0]),int(q["endTs"][0]))).fetchall()
            result={}
            for ts,k,v in rows:
                if k in q["keys"][0].split(","):
                    result.setdefault(k,[]).append(dict(ts=ts,value=v))
            body=json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args): pass
    http=ThreadingHTTPServer(("127.0.0.1",0),History)
    thread=threading.Thread(target=http.serve_forever,daemon=True)
    thread.start()
    with tempfile.TemporaryDirectory() as td:
        with socket.socket() as s:
            s.bind(("127.0.0.1",0))
            port=s.getsockname()[1]
        config=Path(td)/"mqtt.conf"
        config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
        broker=None
        subscriber=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        def connected(c,u,f,r,p):
            if not r.is_failure: c.subscribe("v1/gateway/telemetry",qos=1)
        def subscribed(c,u,mid,reasons,p):
            if not any(x.is_failure for x in reasons): subscriptions.set()
        def message(c,u,msg):
            try:
                payload=json.loads(msg.payload)
                assert msg.qos==1
                count=sum(map(len,payload.values()))
                assert 0<count<=50
                with sqlite3.connect(cloud_path) as db:
                    for dev,rows in payload.items():
                        for r in rows:
                            for k,v in r["values"].items():
                                if k in ("sync_mode","sync_state"): continue
                                value=json.dumps(v) if type(v) is bool else str(v)
                                db.execute("INSERT INTO history VALUES(?,?,?,?) ON CONFLICT(device,ts,key) DO UPDATE SET value=excluded.value",
                                    (dev,r["ts"],k,value))
                packets.append(payload)
            except Exception as exc:
                errors.append(repr(exc))
        subscriber.on_connect,subscriber.on_subscribe,subscriber.on_message=connected,subscribed,message
        adapter=MqttRestCloud("127.0.0.1",port,f"http://127.0.0.1:{http.server_port}",
                              {"FARM_WS_01":"test-device"},demo=True,timeout=2)
        db=connect(edge_path)
        clock=Clock()
        sync=Synchronizer(db,adapter,clock=clock,jitter=lambda a,b:0)
        try:
            # Broker unavailable while actual Edge ingestion keeps committing.
            sync.step()
            assert sync.state=="OFFLINE"
            for i in range(1,73):
                clock.advance(300)
                msg=report(i)
                msg.ts_s=clock()//1000
                status,ack=handle(db,MAPPING,TOPIC,uplink(msg))
                assert status=="inserted" and ack
                # No network probe per sample needed: state remains offline.
            before=sync.status()
            assert before["pending"]==72
            broker=subprocess.Popen(["mosquitto","-c",str(config)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            def ready():
                try:
                    with socket.create_connection(("127.0.0.1",port),timeout=.1): return True
                except OSError: return False
            wait(ready)
            subscriber.connect("127.0.0.1",port)
            subscriber.loop_start()
            assert subscriptions.wait(5)
            clock.advance(60)
            for _ in range(20):
                sync.step()
                if not sync.status()["pending"]: break
                # Allow asynchronous MQTT consumer to commit before next read.
                time.sleep(.15)
                clock.advance(5)
            assert not errors,errors
            assert sync.status()["pending"]==0 and sync.state=="ONLINE"
            assert db.execute("SELECT count(*) FROM weather_outbox WHERE state='SYNCED'").fetchone()[0]==72
            assert db.execute("PRAGMA integrity_check").fetchone()==("ok",)
            # Replayed same record still ACKed, never re-added to pending.
            duplicate=report(1)
            duplicate.ts_s=(clock.now-(clock.now-1795453200000)+300000)//1000
            assert handle(db,MAPPING,TOPIC,uplink(duplicate))[0]=="duplicate"
            with sqlite3.connect(cloud_path) as reader:
                rows=reader.execute("SELECT ts,value FROM history WHERE key='record_id' ORDER BY ts").fetchall()
                rain=reader.execute("SELECT sum(CAST(value AS REAL)) FROM history WHERE key='rainfall'").fetchone()[0]
                assert rows==[(1795453200000+i*300000,f"FARM_WS_01:{i}") for i in range(1,73)]
                assert abs(rain-72*2.5146)<1e-8
                assert reader.execute("PRAGMA integrity_check").fetchone()==("ok",)
            assert packets and all(sum(map(len,p.values()))<=50 for p in packets)
            return dict(result="PASS",scope="D04 logical six-hour outage; actual MQTT/HTTP and SQLite test Cloud, not ThingsBoard PE or RF",
                offline=before,recovered=sync.status(),unique_cloud_records=len(rows),rainfall_mm=round(rain,4),
                expected_rainfall_mm=181.0512,original_timestamps=True,duplicate_replay=True,
                mqtt_batch_sizes=[sum(map(len,p.values())) for p in packets],
                record_ids=[r[1] for r in rows])
        finally:
            adapter.close()
            subscriber.disconnect()
            subscriber.loop_stop()
            if broker:
                broker.terminate()
                broker.wait(timeout=5)
            db.close()
            http.shutdown()
            http.server_close()
            thread.join(timeout=5)

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("edge",type=Path)
    p.add_argument("cloud",type=Path)
    a=p.parse_args()
    print(json.dumps(run(a.edge,a.cloud),indent=2,allow_nan=False))
