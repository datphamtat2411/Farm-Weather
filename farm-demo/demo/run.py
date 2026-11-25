"""Cloud-runner demo console: deterministic logical time, actual native core,
Gateway decoding/commit and Synchronizer. Isolated SQLite Cloud readback model;
not Wokwi, ThingsBoard, physical RF or a production control endpoint.
"""
import argparse
import base64
import datetime as dt
import html
import json
import math
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "edge"))
from gateway import handle
from outbox import connect
from protocol import decode
from sync import Synchronizer, CloudUnavailable, compare, values

EPOCH = 1795453200
DEVICE = "FARM_WS_01"
APP, DEV = "farm-demo", "0000000000000001"
TOPIC = f"application/{APP}/device/{DEV}/event/up"
MAPPING = {f"{APP}/device/{DEV}": DEVICE}
PRESETS = ROOT / "demo" / "scenarios"
DEFAULTS = dict(temperature=25, humidity=65, pressure=1008, direction=135,
                battery_mv=3300, wind_hz=3, rain_tips=0, noise=False,
                ambient_ok=True, wind_ok=True, lora=True, lte=True, confirm=False)
DIRECTIONS = {0:33000,45:8200,90:1000,135:2200,180:3900,225:16000,270:120000,315:64900}
BOOLS = {"noise","ambient_ok","wind_ok","lora","lte","confirm"}
BOUNDS = {"temperature":(-40,85),"humidity":(0,100),"pressure":(300,1100),
          "battery_mv":(2000,3650),"wind_hz":(0,30),"rain_tips":(0,10)}
INTEGERS = {"battery_mv","wind_hz","rain_tips","direction"}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def load_plan(raw):
    require(isinstance(raw,dict) and set(raw)=={"name","segments"},"expected name and segments")
    require(isinstance(raw["name"],str) and 0<len(raw["name"])<=100,"invalid name")
    segments=raw["segments"]
    require(isinstance(segments,list) and 0<len(segments)<=256,"invalid segments")
    controls=DEFAULTS.copy()
    total=0
    expanded=[]
    for segment in segments:
        require(isinstance(segment,dict) and set(segment)=={"seconds","set"},"invalid segment fields")
        duration=segment["seconds"]
        require(type(duration) is int and duration>0,"duration must be positive integer")
        total+=duration
        require(total<=43200,"scenario exceeds 12 logical hours")
        updates=segment["set"]
        require(isinstance(updates,dict) and set(updates)<=set(DEFAULTS),"unknown control")
        # Rain and inspection are momentary inputs, not sticky settings.
        controls.update(rain_tips=0,confirm=False)
        for k,v in updates.items():
            if k in BOOLS:
                require(type(v) is bool,f"{k}: expected boolean")
            elif k=="direction":
                require(type(v) is int and v in DIRECTIONS,"direction must be an eight-point bearing")
            else:
                lo,hi=BOUNDS[k]
                require(type(v) in (int,float) and math.isfinite(v) and lo<=v<=hi
                        and (k not in INTEGERS or type(v) is int),f"{k}: invalid value")
            controls[k]=v
        expanded.append((duration,controls.copy()))
    return raw["name"],expanded

class DemoCloud:
    """Independent persisted history model, with a controlled 4G boundary."""
    def __init__(self,path):
        self.db=sqlite3.connect(path)
        self.db.execute("CREATE TABLE history(device TEXT, ts INTEGER, key TEXT, value TEXT, PRIMARY KEY(device,ts,key))")
        self.available=True
    def connect(self):
        if not self.available: raise CloudUnavailable("simulated 4G offline")
    def close(self): pass
    def publish(self,packet):
        self.connect()
        with self.db:
            for device,rows in packet.items():
                for row in rows:
                    for key,value in row["values"].items():
                        if key in ("sync_mode","sync_state"): continue
                        self.db.execute("INSERT INTO history VALUES(?,?,?,?) ON CONFLICT(device,ts,key) DO UPDATE SET value=excluded.value",
                                        (device,row["ts"],key,json.dumps(value,allow_nan=False)))
    def read(self,packet):
        self.connect()
        result={}
        for device,rows in packet.items():
            for row in rows:
                data=self.db.execute("SELECT key,value FROM history WHERE device=? AND ts=?",(device,row["ts"]))
                result[(device,row["ts"])]={k:json.loads(v) for k,v in data}
        return result

def compile_station(path):
    subprocess.run(["g++","-std=c++17","-O2","-Wall","-Wextra","-Werror",
                    "-fsanitize=undefined","-fno-sanitize-recover=all",
                    "-I",str(ROOT/"firmware/include"),str(ROOT/"demo/station.cpp"),"-o",str(path)],check=True)

def render(report):
    def esc(v): return html.escape(str(v))
    rows=[]
    for point in report["timeline"]:
        rows.append("<tr>"+"".join("<td>"+esc(point[k])+"</td>" for k in
                    ("second","input","status","period","buzzer","stop","station_pending","edge_pending","cloud_state","cloud_periodic"))+"</tr>")
    details=html.escape(json.dumps(report["records"],ensure_ascii=False,indent=2))
    return ("<!doctype html><html lang='vi'><meta charset='utf-8'><title>FarmWeather demo</title>"
            "<style>body{font:16px system-ui;margin:32px;color:#18342c;background:#f4f8f6}"
            "table{border-collapse:collapse;width:100%;background:white}td,th{padding:8px;border:1px solid #d0ddd5;text-align:left}"
            "th{background:#d9eae0}pre{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#53685e}</style>"
            "<h1>"+esc(report["name"])+"</h1><p><strong>MÔ PHỎNG — native firmware / SQLite Cloud model</strong></p>"
            "<p>Không phải kết quả Wokwi, ThingsBoard hay đo RF thực tế. Thời gian logic tính bằng giây.</p>"
            "<p>Records: "+esc(len(report["records"]))+" · Verified: "+esc(report["verified"])+"</p>"
            "<table><thead><tr><th>Giây</th><th>Đầu vào</th><th>Status</th><th>Kỳ (s)</th><th>Còi</th>"
            "<th>Relay yêu cầu dừng</th><th>Flash chờ</th><th>Edge chờ</th><th>Cloud</th><th>Bản ghi định kỳ Cloud</th></tr></thead><tbody>"
            +"".join(rows)+"</tbody></table><details><summary>Bản ghi giải mã từ Protobuf</summary><pre>"+details+"</pre></details></html>")

def run(plan, executable, output, pace=0):
    name,segments=load_plan(plan)
    # New isolated run is reset; never truncate or reuse an existing output.
    output.mkdir(parents=True,exist_ok=False)
    records=[]; timeline=[]; ack="-"; clock=EPOCH*1000
    edge=connect(output/"edge.sqlite")
    cloud=DemoCloud(output/"cloud-model.sqlite")
    worker=Synchronizer(edge,cloud,clock=lambda:clock,jitter=lambda a,b:0)
    proc=subprocess.Popen([str(executable)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,bufsize=1)
    seqs=set(); total_ack=0; duplicates=0; last_point=None; second=0
    try:
        proc.stdin.write(f"{EPOCH}\n");proc.stdin.flush()
        for duration,c in segments:
            for index in range(duration):
                second+=1;clock=(EPOCH+second)*1000
                cloud.available=c["lte"]
                direction=round(4095*DIRECTIONS[c["direction"]]/(10000+DIRECTIONS[c["direction"]]))
                adc=round(c["battery_mv"]*4095/6600)
                if not c["lora"]: ack="-"
                inputs=[c["wind_hz"],c["rain_tips"],int(c["noise"]),direction,adc,c["temperature"],
                        c["humidity"],c["pressure"],int(c["ambient_ok"]),int(c["wind_ok"]),int(c["lora"]),
                        int(c["confirm"]),ack]
                proc.stdin.write(" ".join(map(str,inputs))+"\n");proc.stdin.flush();ack="-"
                recorded=False
                while True:
                    line=proc.stdout.readline()
                    require(bool(line),"station ended before state")
                    parts=line.split()
                    if parts[0]=="RECORD":
                        rec=decode(int(parts[1]),bytes.fromhex(parts[2]),DEVICE)
                        require(rec["sequence_number"] not in seqs,"reused sequence")
                        seqs.add(rec["sequence_number"]);records.append(rec);recorded=True
                    elif parts[0]=="TX":
                        fcnt,port,wire,delivered=parts[1:]
                        require(bool(int(delivered))==c["lora"],"radio state mismatch")
                        if delivered=="1":
                            raw=json.dumps(dict(deviceInfo=dict(applicationId=APP,devEui=DEV),
                                fPort=int(port),fCnt=int(fcnt),data=base64.b64encode(bytes.fromhex(wire)).decode(),
                                time=dt.datetime.fromtimestamp(clock/1000,dt.timezone.utc).isoformat(),
                                rxInfo=[],simulation=True,timeTrusted=True)).encode()
                            result,down=handle(edge,MAPPING,TOPIC,raw)
                            require(result in ("inserted","duplicate") and down is not None,"gateway rejected station data")
                            duplicates+=result=="duplicate"
                            ack=base64.b64decode(down[1]["data"]).hex()
                    elif parts[0]=="ACK":
                        total_ack+=int(parts[1])
                    elif parts[0]=="STATE":
                        stamp,status,period,pending,buzzer,stop=map(int,parts[1:])
                        require(stamp==second,"logical clock mismatch")
                        break
                    else: raise ValueError("unexpected station output")
                # Advance the real worker on logical seconds. Offline probes/backoff
                # stay in the actual Synchronizer, not in the presentation layer.
                if index==0 or recorded or ack!="-" or second%5==0:
                    worker.step()
                point=dict(second=second,input=c.copy(),status=status,period=period,
                           buzzer=bool(buzzer),stop=bool(stop),station_pending=pending,
                           edge_pending=edge.execute("SELECT count(*) FROM weather_outbox WHERE state IN ('PENDING','IN_FLIGHT')").fetchone()[0],
                           cloud_state=worker.state,
                           cloud_periodic=cloud.db.execute("SELECT count(*) FROM history WHERE key='record_id'").fetchone()[0])
                # Preserve segment boundaries, changes, and minute snapshots; avoid
                # thousands of repeated buzzer-cycle rows in six-hour presentations.
                signature=tuple(point[k] for k in ("status","period","stop","station_pending","edge_pending","cloud_state","cloud_periodic"))
                if index in (0,duration-1) or recorded or signature!=last_point or second%60==0:
                    timeline.append(point);last_point=signature
                if pace: time.sleep(1/pace)
        proc.stdin.close()
        require(proc.wait(timeout=10)==0,"native station failed")
        verified=0
        for rec in records:
            packet={DEVICE:[{"ts":rec["timestamp"],"values":values(rec)}]}
            # Read independent persisted history even when the presentation's
            # simulated network is down; do not mark any outbox row SYNCED here.
            saved=cloud.db.execute("SELECT key,value FROM history WHERE device=? AND ts=?",
                                   (DEVICE,rec["timestamp"]))
            if compare(values(rec),{k:json.loads(v) for k,v in saved})=="verified": verified+=1
        require(edge.execute("PRAGMA integrity_check").fetchone()==("ok",),"edge integrity failure")
        require(cloud.db.execute("PRAGMA integrity_check").fetchone()==("ok",),"cloud integrity failure")
        report=dict(name=name,scope="native core + actual Edge SQLite and synchronizer + isolated SQLite Cloud model",
                    logical_seconds=second,records=records,timeline=timeline,verified=verified,
                    station_acks=total_ack,duplicates=duplicates)
        (output/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
        (output/"report.html").write_text(render(report),encoding="utf-8")
        return report
    finally:
        if proc.poll() is None:
            proc.kill();proc.wait()
        proc.stdin.close();proc.stdout.close()
        edge.close();cloud.db.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--scenario",type=Path)
    source.add_argument("--all",action="store_true")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--pace",type=float,default=0,help="logical seconds per wall second; 0 = as fast as possible")
    args=parser.parse_args()
    require(math.isfinite(args.pace) and (args.pace==0 or 1<=args.pace<=100),"pace must be 0 or 1–100")
    with tempfile.TemporaryDirectory() as tmp:
        exe=Path(tmp)/"station"
        compile_station(exe)
        if args.all:
            args.output.mkdir(parents=True,exist_ok=False)
            for scenario in sorted(PRESETS.glob("*.json")):
                run(json.loads(scenario.read_text()),exe,args.output/scenario.stem,args.pace)
        else:
            run(json.loads(args.scenario.read_text()),exe,args.output,args.pace)

if __name__=="__main__":
    main()
