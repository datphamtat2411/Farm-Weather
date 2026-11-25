"""Fail-closed Task 17 evidence manifest. No external network or secrets."""
import hashlib
import json
import sys
from pathlib import Path
from scenarios import load

def req(ok,message):
    if not ok: raise ValueError(message)

def report(folder,name):
    path=folder/name
    req(path.is_file() and path.stat().st_size>0,f"missing {folder.name}/{name}")
    data=json.loads(path.read_text(encoding="utf-8"))
    req(data.get("result")=="PASS",f"not PASS: {folder.name}/{name}")
    return data

def validate(root,sha,output):
    folders={}
    def artifact(name,attempt=False):
        if attempt:
            matches=sorted(root.glob(name+"-attempt-*"),key=lambda p:int(p.name.rsplit("-",1)[-1]))
            req(bool(matches),"missing "+name)
            folder=matches[-1]
        else:
            folder=root/name
        req(folder.is_dir(),"missing "+name)
        req((folder/"commit.txt").read_text().strip()==sha,"wrong commit "+folder.name)
        folders[name]=folder
        return folder
    native=artifact("native-evidence")
    tb=artifact("thingsboard-evidence")
    security=artifact("security-evidence")
    for name in ("firmware-bluepill_f103c8","firmware-nucleo_l476rg","wokwi-preflight"):
        artifact(name)
    for entry in load():
        ident=entry["id"]
        folder=artifact("simulation-"+ident,True)
        for name in (entry["serial"],entry["console"]):
            req((folder/name).stat().st_size>0,"empty simulator log")
        for check in entry["checks"]:
            report(folder,check["report"])
        report(folder,ident+"-wire.json")
    demo=report(native,"task16-report.json")
    req(set(demo["scenarios"])=={"normal","weather-fault","lte-outage","lora-outage","power"},"incomplete demo set")
    report(native,"protocol-report.json")
    report(native,"edge-mqtt-report.json")
    report(native,"d04-report.json")
    cloud=report(tb,"thingsboard-report.json")
    req(cloud["unique_periodic_records"]==72 and cloud["exact_readback"] and cloud["duplicate_replay"] and cloud["restart_persistence"],"D04 Cloud recovery incomplete")
    report(tb,"rule-engine-report.json")
    report(security,"security-report.json")
    ui=json.loads((security/"dashboard-report.json").read_text())
    req(all(ui.get(k) is True for k in ("history_verified","native","responsive","verified_gateway","real_rpc_admission")),"dashboard acceptance missing")
    for name in ("dashboard-viewer.png","dashboard-operator.png","history-viewer.png","history-operator.png","weather-history.csv"):
        req((security/name).stat().st_size>0,"missing UI artifact "+name)
    replay=artifact("task17-replay-evidence",True)
    r=report(replay,"task17-replay.json")
    req(r["commit"]==sha and r["unique_records"]>0 and r["duplicates"]>0 and all(r[k] is True for k in
        ("original_timestamps","exact_readback","historical_alarm_isolation","scoped_csv","browser_history")),"replay incomplete")
    for name in ("task17-recovered-dashboard.png","task17-recovered-history.csv","task17-recovered-history.json"):
        req((replay/name).stat().st_size>0,"missing replay artifact")
    checks={
        "D01":["simulation-weather","task17-replay-evidence","security-evidence"],
        "D02":["simulation-local-alarms","thingsboard-evidence"],
        "D03":["simulation-local-alarms","simulation-invalid-ambient","native-evidence"],
        "D04":["native-evidence","thingsboard-evidence"],
        "D05":["simulation-link-recovery","task17-replay-evidence"],
        "D06":["simulation-power","native-evidence"],
        "D07":["simulation-remote-control","security-evidence"],
    }
    evidence={}
    for name,folder in folders.items():
        evidence[name]={"artifact":folder.name,"files":{
            str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}}
    data={"result":"PASS","commit":sha,"scenarios":checks,"evidence":evidence,
          "scope":"isolated non-production integration and actual Wokwi; simulated RF/4G; no public hosting or physical qualification"}
    (output/"acceptance.json").write_text(json.dumps(data,indent=2),encoding="utf-8")
    lines=["# Task 17 — Functional acceptance","",f"Commit: `{sha}`","",
           "| Scenario | Result | Evidence artifacts |","|---|---|---|"]
    lines.extend(f"| {k} | PASS | {', '.join(folders[n].name for n in names)} |" for k,names in checks.items())
    lines.extend(["","Actual Wokwi + real isolated ThingsBoard, PostgreSQL and browser. Recorded wire replay; no live RF, measured power, or public deployment claim.",
                  "Public hosting and licensed production use require a separately provisioned environment. See handover runbook."])
    (output/"acceptance.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

if __name__=="__main__":
    root,sha,output=sys.argv[1:]
    validate(Path(root),sha,Path(output))
