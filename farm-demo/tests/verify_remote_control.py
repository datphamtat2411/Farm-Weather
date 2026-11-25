"""Decode actual FPort 22 outputs from the explicit hardware acceptance scenario."""
import json
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"edge"))
from rpc import decode_result

log=Path(sys.argv[1]).read_text()
frames=re.findall(r"RADIO_TX seq=(\d+) fcnt=\d+ fport=22 hex=([0-9a-f]+) delivered=1 ts=\d+",log)
results={}
for seq,hexdata in frames:
    record=decode_result(bytes.fromhex(hexdata))
    assert record["sequence"]==int(seq)
    if seq in results: assert results[seq]==record
    results[seq]=record
records=list(results.values())
assert [r["state"] for r in records]==["APPLIED","UNKNOWN","APPLIED","EXPIRED","APPLIED"],records
assert records[0]["buzzer"] and not records[1]["buzzer"]
assert records[0]["command_id"]==records[1]["command_id"]
assert records[2]["stop_request"] and records[3]["stop_request"] and not records[3]["buzzer"]
assert records[4]["config_version"]==2 and records[4]["wind_alarm_on"]==9
assert records[4]["wind_alarm_off"]==6 and records[4]["applied_period_s"]==900
assert "HARDWARE_ERROR" not in log and "RPC_STORAGE_UNAVAILABLE" not in log
print(json.dumps({"result":"PASS","scope":"Wokwi logical Class A and simulated output pins","commands":records},indent=2))
