"""Check actual Wokwi log; independent of the engineering energy budget."""
import json, math, re, sys
from pathlib import Path

def verify(log):
    def req(ok,msg):
        if not ok: raise ValueError(msg)
    req("READY FARM_WS_01 SIMULATION" in log and "HARDWARE_ERROR" not in log,"bad boot")
    records=[(k,json.loads(v)) for k,v in re.findall(r'(EVENT|TELEMETRY) (\{[^{}\r\n]*\})',log)]
    req([r["sequence_number"] for _,r in records]==list(range(1,len(records)+1)),"sequence gap")
    rows=[r for k,r in records if k=="TELEMETRY"]
    events=[r for k,r in records if k=="EVENT"]
    req([r["period_s"] for r in rows]==[300,300,300,900,300],"wrong reporting windows")
    req([r["ts_s"]-1795453200 for r in rows]==[300,600,900,1800,2100],"wrong timestamps")
    req([r["next_period_s"] for r in rows]==[300,300,900,300,300],"wrong requested period")
    req([bool(r["status"]&8) for r in rows]==[False,False,True,False,False],"wrong battery state")
    for r in rows:
        t=r["ts_s"]-1795453200
        req(r["rtc_wakes"]==t and r["ambient_samples"]==1+t//60 and r["direction_samples"]==1+t//5 and r["battery_samples"]==t//300,"sampling cadence changed")
        req(r["sim_sleep_us"]>r["sim_active_us"]>0 and r["pulse_wakes"]>0 and r["sleep_entries"]>=t,"no logical sleep/wake activity")
        req(abs(r["sim_sleep_us"]+r["sim_active_us"]-t*40000)<500000,"sleep/active accounting gap")
        req(r["valid_mask"]==127,"invalid sensor in power scenario")
    req(all(r["battery_voltage"]<=3 for r in rows[:3]) and all(r["battery_voltage"]>=3.2 for r in rows[3:]),"wrong battery inputs")
    req(math.isclose(rows[3]["rainfall"],0.2794,abs_tol=1e-5) and rows[3]["wind_gust"]>=10 and rows[3]["wind_speed"]>0,"lost measurements in 900s window")
    req(all(r["rainfall"]==0 for r in rows[:3]+rows[4:]),"rain counted twice")
    changes=[(r["ts_s"]-1795453200,bool(r["status"]&8)) for r in events if r["changed_mask"]&8]
    req(changes==[(900,True),(1800,False)],"wrong power transitions")
    wind=[r for r in events if r["changed_mask"]&1]
    req(len(wind)==2 and all(900<r["ts_s"]-1795453200<1800 and r["status"]&8 for r in wind) and wind[0]["stop_request"] and not wind[1]["stop_request"],"local alarm stopped in low-power mode")
    return {"result":"PASS","scope":"Wokwi logical power model; not physical current or RF","reports":rows,"events":events}
if __name__=="__main__":
    print(json.dumps(verify(Path(sys.argv[1]).read_text()),indent=2,allow_nan=False))
