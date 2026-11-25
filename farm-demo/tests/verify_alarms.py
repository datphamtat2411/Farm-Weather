"""Validate actual Wokwi transition evidence; no synthetic PASS."""
import json
import math
import re
import sys
from pathlib import Path

def verify(log):
    def require(ok, message):
        if not ok:
            raise ValueError(message)
    require("READY FARM_WS_01 SIMULATION" in log and "HARDWARE_ERROR" not in log, "bad boot")
    records=[(kind,json.loads(obj)) for kind,obj in re.findall(r'(EVENT|TELEMETRY) (\{[^{}\r\n]*\})',log)]
    require(bool(records), "no records")
    require([r["sequence_number"] for _,r in records]==list(range(1,len(records)+1)), "noncontiguous sequence")
    events=[r for kind,r in records if kind=="EVENT"]
    previous=0
    for e in events:
        require(e["device_id"]=="FARM_WS_01" and e["kind"]=="event", "bad event identity")
        require(e["changed_mask"]==previous^e["status"], "incorrect transition mask")
        previous=e["status"]
    def transition(bit,on):
        matches=[e for e in events if e["changed_mask"]&bit and bool(e["status"]&bit)==on]
        require(len(matches)==1, f"expected one transition bit={bit} on={on}, got {len(matches)}")
        return matches[0]
    on,off=transition(1,True),transition(1,False)
    fault,recovered=transition(16,True),transition(16,False)
    require(on["wind_window_mps"]>=10 and on["stop_request"] and on["buzzer"], "wind alarm incorrect")
    require(fault["fault_mask"]&8 and fault["stop_request"] and fault["status"]&1, "wind failure released protection")
    require(recovered["ts_s"]<off["ts_s"] and off["ts_s"]-recovered["ts_s"]>=59, "wind recovery too early")
    require(off["wind_window_mps"]<7 and not off["stop_request"] and not off["buzzer"] and off["mode"]=="AWAIT_INSPECTION", "bad wind clear")
    confirmations=[e for e in events if e["changed_mask"]==0 and e["mode"]=="NORMAL"]
    require(len(confirmations)==1 and confirmations[0]["ts_s"]>off["ts_s"], "missing inspection confirmation")
    rain_on,rain_off=transition(2,True),transition(2,False)
    require(math.isclose(rain_on["rain_10min_mm"],5.0292,abs_tol=0.00001), "rain threshold/count mismatch")
    require(rain_off["rain_10min_mm"]<2, "rain cleared too soon")
    heat_on,heat_off=transition(4,True),transition(4,False)
    require(heat_on["temperature"]>=35 and heat_off["temperature"]<33 and heat_off["ts_s"]-heat_on["ts_s"]>=180, "heat recovery invalid")
    for e in (rain_on,rain_off,heat_on,heat_off):
        require(not e["stop_request"] and not e["buzzer"], "advisory alarm operated actuators")
    require(any(kind=="TELEMETRY" for kind,_ in records), "missing periodic telemetry")
    return {"result":"PASS","scope":"Task 04 simulated local alarm and actuator transitions","events":events,"record_count":len(records)}
if __name__=="__main__":
    print(json.dumps(verify(Path(sys.argv[1]).read_text(encoding="utf-8")),indent=2,allow_nan=False))
