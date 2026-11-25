"""Validate a real Wokwi negative scenario, never a fabricated telemetry fixture."""
import json
import re
import sys
from pathlib import Path

def main(path):
    log=Path(path).read_text(encoding="utf-8")
    if "READY FARM_WS_01 SIMULATION" not in log or "HARDWARE_ERROR" in log:
        raise ValueError("Wokwi firmware did not start cleanly")
    records=re.findall(r'TELEMETRY\s+(\{[^\r\n]*\})',log)
    if len(records)!=1:
        raise ValueError(f"expected one invalid-ambient record, got {len(records)}")
    r=json.loads(records[0])
    if r.get("device_id")!="FARM_WS_01" or r.get("sequence_number")!=2:
        raise ValueError("wrong station/record")
    if r.get("ts_s")!=1795453500 or r.get("period_s")!=300:
        raise ValueError("wrong measurement timestamp/period")
    if r.get("valid_mask",0)&1 or "temperature" in r:
        raise ValueError("invalid BME280 temperature was published as a fresh value")
    if (r.get("valid_mask",0)&(2|4))!=(2|4):
        raise ValueError("valid humidity/pressure incorrectly discarded")
    if not (r.get("status",0)&16):
        raise ValueError("three consecutive invalid minute samples did not raise SENSOR fault")
    return {"result":"PASS","scope":"Wokwi simulated BME280 out-of-range",
            "temperature_omitted":True,"sensor_fault":True,
            "valid_mask":r["valid_mask"],"status":r["status"],"timestamp":r["ts_s"]}

if __name__=="__main__":
    print(json.dumps(main(sys.argv[1]),indent=2))
