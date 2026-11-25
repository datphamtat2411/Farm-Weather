"""Verify native or actual Wokwi wire bytes with Google's generated decoder."""
import json
import math
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "edge"))
from protocol import decode, compact_json, WeatherRecord, WeatherEvent

def check(ok, message):
    if not ok:
        raise ValueError(message)

def native(log):
    frames={}
    for line in log.splitlines():
        name, port, data=line.split()
        check(name not in frames,"duplicate vector")
        frames[name]=(int(port),bytes.fromhex(data))
    check(set(frames)=={"report","negative_zero","invalid","bounds","event","event_missing"} | {f"mask_{n}" for n in range(128)},"missing vector")
    decoded={name:decode(port,payload,"FARM_WS_01") for name,(port,payload) in frames.items()}
    expected=dict(device_id="FARM_WS_01",sequence_number=1052,timestamp=1795453200000,period_s=300,
                  temperature=29.5,humidity=78.2,pressure=1008.4,wind_speed=6.3,wind_gust=8.8,
                  wind_direction=135,rainfall=2.5146,battery_voltage=3.2,status=0,valid_mask=127)
    check(decoded["report"]==expected,"report vector mismatch")
    golden=WeatherRecord(sequence_number=1052,ts_s=1795453200,period_s=300,temp_x10=295,
                         humidity_x10=782,pressure_x10=10084,wind_x10=63,gust_x10=88,
                         direction_deg=135,rain_tips=9,battery_mv=3200,status=0,valid_mask=127)
    check(golden.SerializeToString(deterministic=True)==frames["report"][1],"protoc reference bytes mismatch")
    groups=(("temperature",),("humidity",),("pressure",),("wind_speed","wind_gust"),
            ("wind_direction",),("rainfall",),("battery_voltage",))
    for mask in range(128):
        label=f"mask_{mask}"
        expected_partial=dict(expected,valid_mask=mask)
        reference=WeatherRecord()
        reference.CopyFrom(golden)
        reference.valid_mask=mask
        proto_groups=(("temp_x10",),("humidity_x10",),("pressure_x10",),("wind_x10","gust_x10"),
                      ("direction_deg",),("rain_tips",),("battery_mv",))
        for bit, keys in enumerate(groups):
            if not mask & (1<<bit):
                for key in keys:
                    expected_partial.pop(key)
                for field in proto_groups[bit]:
                    reference.ClearField(field)
        check(decoded[label]==expected_partial,"mask round trip "+label)
        check(reference.SerializeToString(deterministic=True)==frames[label][1],"reference mask mismatch "+label)
    n=decoded["negative_zero"]
    check(n["temperature"]==-12.5 and n["period_s"]==900 and all(n[k]==0 for k in ("humidity","wind_speed","wind_gust","wind_direction","rainfall")),"negative/zero round trip")
    check(set(decoded["invalid"])=={"device_id","sequence_number","timestamp","period_s","status","valid_mask"},"invalid fields published")
    check(decoded["bounds"]["sequence_number"]==0xffffffff and decoded["bounds"]["timestamp"]==0xffffffff*1000 and decoded["bounds"]["rainfall"]==round(0xffffffff*0.2794,4),"uint32 boundary")
    event=decoded["event"]
    check(event==dict(device_id="FARM_WS_01",kind="event",sequence_number=1055,timestamp=1795453200000,
                      changed_mask=3,status=3,requested_period_s=300,fault_mask=0,changed_fault_mask=0,
                      mode="ALARM",buzzer=True,stop_request=True,rain_10min_mm=5.0292,
                      wind_window_mps=10.0,temperature=-12.5,battery_voltage=3.2),"event mismatch")
    e=decoded["event_missing"]
    check(e["mode"]=="AWAIT_INSPECTION" and not e["buzzer"] and not e["stop_request"] and
          not any(k in e for k in ("temperature","battery_voltage","wind_window_mps")) and "rainfall" not in e,"event optional fields")
    j=len(compact_json(expected).encode("utf-8"));p=len(frames["report"][1])
    return dict(result="PASS",scope="native STM32 codec and protoc reference, not RF",
                json_bytes=j,protobuf_bytes=p,reduction_percent=round(100*(j-p)/j,2),
                daily_json_bytes=j*288,daily_protobuf_bytes=p*288,
                validity_masks_verified=128,
                vectors={n:dict(fport=f,hex=b.hex(),bytes=len(b),decoded=decoded[n]) for n,(f,b) in frames.items() if not n.startswith("mask_")})

def serial(log):
    records=re.findall(r'(EVENT|TELEMETRY) (\{[^{}\r\n]*\})',log)
    check(bool(records),"missing serial records")
    result=[]
    for label,obj in records:
        r=json.loads(obj)
        port=11 if label=="EVENT" else 10
        check(r.get("fport")==port and not r.get("wire_error"),"missing/invalid FPort")
        wire=r.get("wire_hex")
        check(isinstance(wire,str) and re.fullmatch(r"(?:[0-9a-f]{2})+",wire),"invalid wire hex")
        d=decode(port,bytes.fromhex(wire),r["device_id"])
        check(d["timestamp"]==r["ts_s"]*1000,"timestamp mismatch")
        for key,value in d.items():
            if key=="timestamp":
                continue
            check(key in r,"missing diagnostic field "+key)
            if key in ("temperature","humidity","pressure","wind_speed","wind_gust","wind_window_mps"):
                check(math.isclose(value,r[key],rel_tol=0,abs_tol=0.055001),f"quantization mismatch {key}")
            elif key in ("rainfall","rain_10min_mm","battery_voltage"):
                check(math.isclose(value,r[key],rel_tol=0,abs_tol=0.000051),f"unit mismatch {key}")
            else:
                check(value==r[key],f"metadata mismatch {key}")
        for key in ("temperature","humidity","pressure","wind_speed","wind_gust","wind_direction",
                    "rainfall","battery_voltage","wind_window_mps"):
            check((key in d)==(key in r),"measurement presence mismatch "+key)
        result.append(dict(fport=port,protobuf_bytes=len(bytes.fromhex(wire)),record=d))
    check([r["record"]["sequence_number"] for r in result]==list(range(1,len(result)+1)),"sequence gap")
    return dict(result="PASS",scope="actual Wokwi firmware wire bytes",records=result)

if __name__=="__main__":
    mode,path=sys.argv[1:]
    log=Path(path).read_text(encoding="utf-8")
    print(json.dumps({"native":native,"serial":serial}[mode](log),indent=2,allow_nan=False))
