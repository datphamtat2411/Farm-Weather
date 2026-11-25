"""Task 10: actual ThingsBoard/PostgreSQL, TLS MQTT + HTTPS, real Edge outbox.
Runs only against a freshly installed isolated non-production instance.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"edge"), str(ROOT/"cloud")]
from cloud import MqttRestCloud
from gateway import handle
from outbox import connect
from sync import Synchronizer, values, compare
from provision import Rest, provision, STATION
from test_gateway import report, event, uplink, MAPPING, TOPIC
from test_sync import Clock

def wait_login(url, ca, timeout=240):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            api = Rest(url, ca)
            api.login(os.environ["FARM_TB_ADMIN_USER"], os.environ["FARM_TB_ADMIN_PASSWORD"])
            return api
        except (OSError, RuntimeError, ValueError, KeyError):
            time.sleep(3)
    raise AssertionError("ThingsBoard TLS/login readiness timeout")

def compose(*args):
    subprocess.run(["docker","compose","-f",str(ROOT/"cloud/compose.yml"),*args],
                   check=True, timeout=120)

def run(path, url, ca, port):
    api = wait_login(url, ca)
    topology, token = provision(api)
    assert provision(api) == (topology, token), "provisioning must be idempotent"
    station_id = topology["devices"][STATION]
    gateway = api.request("GET", "/api/device/"+topology["gateway"])
    assert gateway["additionalInfo"]["gateway"] is True
    assert station_id != topology["gateway"]
    attrs = api.request("GET", f"/api/plugins/telemetry/DEVICE/{station_id}/values/attributes/SHARED_SCOPE")
    assert {x["key"]:x["value"] for x in attrs}["report_period_s"] == 300
    # CI-only disposable tenant JWT; production service credentials are separate (Task 12).
    adapter = MqttRestCloud("127.0.0.1",port,url,topology["devices"],token=token,
                            api_token=api.authorization.removeprefix("Bearer "),ca_file=ca,timeout=10)
    assert not path.exists()
    db = connect(path)
    clock = Clock()
    # Fixed logical timestamps preserve Ch.4 example; 72 samples over six hours.
    sync = Synchronizer(db,adapter,clock=clock,jitter=lambda a,b:0)
    try:
        compose("stop","-t","15","thingsboard")
        sync.step()
        assert sync.state == "OFFLINE"
        for i in range(1,73):
            clock.advance(300)
            msg=report(i)
            msg.ts_s=clock()//1000
            # Cover all seven quality groups for normal weather, and omit invalid ambient for sample 1.
            if i > 1:
                msg.valid_mask=127
                msg.temp_x10=295
                msg.humidity_x10=782
                msg.pressure_x10=10084
                msg.wind_x10=63
                msg.gust_x10=88
                msg.direction_deg=135
                msg.battery_mv=3200
            assert handle(db,MAPPING,TOPIC,uplink(msg))[0]=="inserted"
        e=event(73)
        e.ts_s=clock()//1000
        assert handle(db,MAPPING,TOPIC,uplink(e,11))[0]=="inserted"
        before=sync.status()
        assert before["pending"]==73
        compose("up","-d","thingsboard")
        api=wait_login(url,ca)
        adapter.headers={"X-Authorization":api.authorization}
        clock.advance(60)
        for _ in range(25):
            sync.step()
            if not sync.status()["pending"]:
                break
            time.sleep(.5)
            clock.advance(5)
        assert sync.state=="ONLINE" and sync.status()["pending"]==0, sync.status()
        rows=db.execute("SELECT payload_json FROM weather_outbox WHERE state='SYNCED' ORDER BY sequence_number").fetchall()
        assert len(rows)==73
        expected=[json.loads(row[0]) for row in rows]
        packet={STATION:[{"ts":r["timestamp"],"values":values(r)} for r in expected]}
        actual=adapter.read(packet)
        assert len(actual)==72
        for r in expected:
            assert compare(values(r),actual[(STATION,r["timestamp"])])=="verified",r["sequence_number"]
        assert "temperature" not in actual[(STATION,expected[0]["timestamp"])]
        assert actual[(STATION,expected[-1]["timestamp"])]["event_record_id"]==f"{STATION}:73"
        rain=sum(float(x["rainfall"]) for x in actual.values())
        assert abs(rain-181.0512)<1e-8
        dup=report(1)
        dup.ts_s=expected[0]["timestamp"]//1000
        assert handle(db,MAPPING,TOPIC,uplink(dup))[0]=="duplicate"
        assert sync.status()["pending"]==0
        # PostgreSQL is the only timeseries backend; verify persisted data after restarting TB.
        adapter.close()
        compose("restart","-t","15","thingsboard")
        api=wait_login(url,ca)
        adapter.headers={"X-Authorization":api.authorization}
        adapter.connect()
        assert adapter.read(packet)==actual
        assert db.execute("PRAGMA integrity_check").fetchone()==("ok",)
        return {"result":"PASS","scope":"ThingsBoard 4.4.1 unified non-production, actual PostgreSQL, MQTT/TLS and HTTPS; simulated radio/4G; no physical RF or PE production claim",
                "topology":topology,"offline":before,"recovered":sync.status(),
                "unique_periodic_records":72,"event_records":1,"rainfall_mm":round(rain,4),
                "exact_readback":True,"duplicate_replay":True,"same_timestamp_event":True,
                "invalid_field_absent":True,"restart_persistence":True,
                "record_ids":[r["record_id"] for r in actual.values()]}
    finally:
        adapter.close()
        db.close()

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--db",type=Path,required=True)
    p.add_argument("--url",default="https://127.0.0.1:8443")
    p.add_argument("--ca",required=True)
    p.add_argument("--port",type=int,default=8883)
    a=p.parse_args()
    print(json.dumps(run(a.db,a.url,a.ca,a.port),indent=2,allow_nan=False))
