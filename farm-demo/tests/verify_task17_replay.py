"""Replay actual delivered Wokwi link-recovery bytes through production Edge and ThingsBoard.
This is recorded transport replay, not a live Wokwi-to-Cloud radio connection.
"""
import argparse
import base64
import csv
import datetime as dt
import io
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from verify_thingsboard import initial_setup, wait_login, STATION, MqttRestCloud, provision
from verify_protocol import serial
from verify_link import verify
from provision import Rest
from security import install_security, activate, issue_key, ROLES
from dashboard import install
from farm_api import FarmApi, make_server
from gateway import handle
from outbox import connect
from protocol import decode
from sync import Synchronizer, values, compare
from weather_pb2 import WeatherAck
from test_gateway import APP, DEV, MAPPING, TOPIC
from overview import VN

def require(ok, reason):
    if not ok:
        raise AssertionError(reason)

def select_artifact(root, sha):
    # download-artifact v6 flattens a single match even when pattern is used.
    # Multiple matches keep their named subdirectories. Accept exactly one
    # layout and always verify source SHA and both reports below.
    candidates = sorted((p for p in root.glob("simulation-link-recovery-attempt-*")
                         if p.is_dir()), key=lambda p:int(p.name.rsplit("-",1)[-1]))
    flat=(root/"commit.txt").is_file()
    require(not (flat and candidates),"ambiguous Wokwi artifact layout")
    require(flat or bool(candidates),"missing actual Wokwi artifact")
    folder=root if flat else candidates[-1]
    require((folder/"commit.txt").read_text().strip()==sha,"wrong simulation commit")
    require(json.loads((folder/"link-recovery.json").read_text())["result"]=="PASS","link verifier did not pass")
    require(json.loads((folder/"link-recovery-wire.json").read_text())["result"]=="PASS","wire verifier did not pass")
    return folder/"link-recovery.log"

def delivered_frames(log):
    verify(log)
    diagnostic=serial(log)["records"]
    expected={r["record"]["sequence_number"]:r["record"] for r in diagnostic}
    frames=[]
    for seq,fcnt,port,wire,delivered,stamp in re.findall(
            r"RADIO_TX seq=(\d+) fcnt=(\d+) fport=(\d+) hex=([0-9a-f]+) delivered=([01]) ts=(\d+)",log):
        if delivered!="1":
            continue
        raw=bytes.fromhex(wire)
        record=decode(int(port),raw,STATION)
        require(record==expected[int(seq)],"wire differs from station diagnostics")
        frames.append((int(fcnt),int(port),raw,record))
    require(frames and {r[3]["sequence_number"] for r in frames}==set(expected),"incomplete recovered trace")
    require(len(frames)>len(expected),"no real retry in recovered trace")
    return frames,expected

def run(args):
    source=select_artifact(args.artifacts,args.sha)
    frames,expected=delivered_frames(source.read_text(encoding="utf-8"))
    initial_setup(args.url,args.ca)
    admin=wait_login(args.url,args.ca)
    topology,token=provision(admin)
    station=topology["devices"][STATION]
    emails={name:f"farm-task17-{name}@example.invalid" for name in ROLES}
    manifest,users,_=install_security(admin,topology,emails)
    passwords={name:"Aa1!"+secrets.token_hex(24) for name in ROLES}
    for name in ROLES:
        activate(admin,users[name],passwords[name])
    password=passwords["operator"]
    reader_key=issue_key(admin,users["reader"],"CI replay history reader")["value"]
    control_key=issue_key(admin,users["control"],"CI replay control")["value"]
    adapter=MqttRestCloud("127.0.0.1",args.port,args.url,topology["devices"],token=token,
                          api_key=reader_key,ca_file=args.ca,timeout=10)
    db_path=args.output/"task17-edge.sqlite"
    require(not db_path.exists(),"fresh database required")
    db=connect(db_path)
    duplicate_count=0
    try:
        for fcnt,port,wire,record in frames:
            # The logged test epoch is fixed. Preserve measurement UTC exactly.
            received=dt.datetime.fromtimestamp(record["timestamp"]/1000,dt.timezone.utc).isoformat()
            raw=json.dumps(dict(deviceInfo=dict(applicationId=APP,devEui=DEV),fPort=port,
                fCnt=fcnt,data=base64.b64encode(wire).decode(),time=received,
                rxInfo=[],simulation=True,timeTrusted=True)).encode()
            outcome,down=handle(db,MAPPING,TOPIC,raw)
            require(outcome in ("inserted","duplicate") and down,"Edge rejected actual wire")
            duplicate_count+=outcome=="duplicate"
            ack=WeatherAck.FromString(base64.b64decode(down[1]["data"]))
            require(list(ack.sequence_number)==[record["sequence_number"]],"wrong application ACK")
            with sqlite3.connect(db_path) as reader:
                row=reader.execute("SELECT payload_json,state FROM weather_outbox WHERE device_id=? AND sequence_number=?",
                    (STATION,record["sequence_number"])).fetchone()
            require(row and json.loads(row[0])==record and row[1]=="PENDING","ACK before durable exact payload")
        require(duplicate_count>0,"no duplicate verified")
        db.close()
        db=connect(db_path)
        # All replay records are historical. Never translate the firmware clock or
        # create fresh alarms from a recorded trace. Advance only the retry clock.
        clock=max(int(time.time()*1000),max(r["timestamp"] for r in expected.values())+3600000)
        worker=Synchronizer(db,adapter,clock=lambda:clock,jitter=lambda a,b:0)
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            worker.step()
            if worker.status()["pending"]==0:
                break
            time.sleep(1)
            clock+=5000
        require(worker.state=="ONLINE" and worker.status()["pending"]==0,"Cloud recovery incomplete")
        require(db.execute("SELECT count(*) FROM weather_outbox WHERE state='SYNCED'").fetchone()[0]==len(expected),
                "not every recovered record verified")
        require(db.execute("PRAGMA integrity_check").fetchone()==("ok",),"SQLite integrity")
        packet={STATION:[dict(ts=r["timestamp"],values=values(r)) for r in expected.values()]}
        actual=adapter.read(packet)
        for r in expected.values():
            require(compare(values(r),actual.get((STATION,r["timestamp"]),{}))=="verified","Cloud content mismatch")
        active=admin.request("GET",f"/api/alarm/DEVICE/{station}",pageSize=100,page=0,searchStatus="ACTIVE")["data"]
        require(not any(a["type"]=="WIND_HIGH" for a in active),"historical wind alarm replayed as live")
        periodic=sorted((r for r in expected.values() if r.get("kind")!="event"),key=lambda r:r["timestamp"])
        require(bool(periodic),"missing periodic history")
        start=min(r["timestamp"]-r["period_s"]*1000 for r in periodic)//60000*60000
        end=(max(r["timestamp"] for r in periodic)//60000+1)*60000
        body={"startTs":start,"endTs":end}
        client=Rest(args.url,args.ca)
        client.login(emails["operator"],password)
        app=FarmApi(args.url,args.ca,manifest,control_key,args.output/"task17-audit.sqlite")
        tls=Path(args.ca).parent
        server=make_server(app,"127.0.0.1",0,str(tls/"server.crt"),str(tls/"server.key"))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        endpoint=f"https://127.0.0.1:{server.server_port}"
        def call(action):
            return Rest(endpoint,args.ca,authorization=client.authorization).request(
                "POST",f"/api/farm/stations/{station}/{action}",body)
        try:
            history=call("history")
            ids=[f"{STATION}:{r['sequence_number']}" for r in periodic]
            require([r["record_id"] for r in history["records"]]==ids,"scoped dashboard history mismatch")
            exported=call("export")
            require([r["record_id"] for r in csv.DictReader(io.StringIO(exported["csv"]))]==ids,"CSV identity mismatch")
            (args.output/"task17-recovered-history.csv").write_text(exported["csv"],encoding="utf-8")
            (args.output/"task17-recovered-history.json").write_text(json.dumps(history,indent=2),encoding="utf-8")
            dashboard_id=install(admin,topology,manifest)
            from playwright.sync_api import sync_playwright,expect
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                context=browser.new_context(ignore_https_errors=True,viewport={"width":1440,"height":1000})
                page=context.new_page()
                def forward(route):
                    request=route.request
                    require(request.headers.get("x-authorization","").startswith("Bearer "),"missing browser JWT")
                    route.fulfill(response=route.fetch(url=endpoint+urlsplit(request.url).path))
                page.route("**/api/farm/stations/**",forward)
                page.goto(args.url+"/login",wait_until="domcontentloaded")
                page.locator("#username-input").fill(emails["operator"])
                page.locator("#password-input").fill(password)
                with page.expect_response(lambda r:r.url.endswith("/api/auth/login") and r.status==200):
                    page.locator("form.tb-login-form button[type=submit]").click()
                page.wait_for_url(lambda u:"/login" not in u,timeout=60000)
                page.goto(args.url+"/dashboards/"+dashboard_id,wait_until="domcontentloaded")
                expect(page.locator(".fw-shell")).to_be_visible(timeout=60000)
                page.locator("#fw-history-tab").click()
                form=page.locator("#fw-history-form")
                form.locator('[name="range"]').select_option("custom")
                for name,ts in [("start",start),("end",end)]:
                    form.locator(f'[name="{name}"]').fill(dt.datetime.fromtimestamp(ts/1000,VN).strftime("%Y-%m-%dT%H:%M"))
                form.get_by_role("button",name="Xem thống kê").click()
                expect(page.locator("#fw-history-rows tbody tr")).to_have_count(len(ids),timeout=60000)
                for ident in ids:
                    expect(page.locator("#fw-history-rows")).to_contain_text(ident)
                page.locator(".fw-shell").screenshot(path=str(args.output/"task17-recovered-dashboard.png"))
                context.close()
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
        return {"result":"PASS","commit":args.sha,"scope":"actual Wokwi delivered wire replay, Edge SQLite, MQTT/TLS, native ThingsBoard PostgreSQL, scoped HTTPS, Chromium history; not live RF",
                "unique_records":len(expected),"duplicates":duplicate_count,"periodic_records":len(periodic),
                "original_timestamps":True,"exact_readback":True,"historical_alarm_isolation":True,
                "scoped_csv":True,"browser_history":True,"record_ids":ids}
    finally:
        adapter.close()
        db.close()

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--artifacts",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--sha",required=True)
    p.add_argument("--ca",required=True)
    p.add_argument("--url",default="https://127.0.0.1:8443")
    p.add_argument("--port",type=int,default=8883)
    print(json.dumps(run(p.parse_args()),indent=2,allow_nan=False))
