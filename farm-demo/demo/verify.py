"""Fail-closed development gate for the presentation runner's built-in scenarios."""
import json
import math
import sys
from pathlib import Path

def req(ok,msg):
    if not ok: raise AssertionError(msg)
def verify(root):
    reports={}
    for name in ("normal","weather-fault","lte-outage","lora-outage","power"):
        data=json.loads((root/name/"report.json").read_text())
        records=data["records"];points=data["timeline"]
        req(records and points and data["verified"]==len(records),name+": incomplete Cloud readback")
        req(data["station_acks"]==len(records),name+": station ACK missing")
        req(points[-1]["station_pending"]==points[-1]["edge_pending"]==0,name+": pending at end")
        periodic=[r for r in records if r.get("kind")!="event"]
        events=[r for r in records if r.get("kind")=="event"]
        if name=="normal":
            req(len(periodic)==2 and all(r["valid_mask"]==127 for r in periodic),"normal quality/count")
            req(periodic[0]["temperature"]==29.5 and periodic[0]["wind_speed"]==6,"normal input not propagated")
        elif name=="weather-fault":
            req(any(e["changed_mask"]&1 and e["stop_request"] for e in events),"wind alarm missing")
            req(any(e["changed_mask"]&16 and e["status"]&16 for e in events),"sensor fault missing")
            req(any(e["changed_mask"]&2 and math.isclose(e["rain_10min_mm"],5.0292) for e in events),"rain/debounce mismatch")
            req(math.isclose(sum(r["rainfall"] for r in periodic),5.0292),"rain duplicated")
            req(any(e["changed_mask"]&4 and e["status"]&4 for e in events),"temperature alarm missing")
            req(any(not p["stop"] and p["second"]>=85 for p in points),"wind recovery missing")
        elif name=="lte-outage":
            req(len(periodic)==72,"six hours must create exactly 72 records")
            req(any(p["edge_pending"]==72 and p["cloud_periodic"]==0 for p in points),"4G isolation failed")
            req(points[-1]["cloud_periodic"]==72,"missing recovered records")
        elif name=="lora-outage":
            req(any(p["station_pending"]>0 and p["edge_pending"]==0 and not p["input"]["lora"] for p in points),"station buffering absent")
            req(any(p["stop"] and not p["input"]["lora"] for p in points),"offline actuation absent")
        elif name=="power":
            req([r["period_s"] for r in periodic]==[300,300,300,900,300],"power period sequence")
            req(math.isclose(sum(r["rainfall"] for r in periodic),0.2794),"rain lost in low power")
            req(any(p["period"]==900 and p["stop"] for p in points),"alarm unavailable in low power")
        reports[name]={"periodic":len(periodic),"events":len(events),"verified":data["verified"]}
    return reports
if __name__=="__main__":
    print(json.dumps({"result":"PASS","scope":"Task 16 native demo runner, not hardware acceptance","scenarios":verify(Path(sys.argv[1]))},indent=2))
