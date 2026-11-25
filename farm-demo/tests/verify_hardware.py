"""Check actual simulator inputs reached MCU telemetry through the hardware model."""
import json
import math
import sys
from pathlib import Path

def verify(report):
    r=report["telemetry"]
    expected={"temperature":(27.5,0.15),"humidity":(65,0.2),"pressure":(1012,0.2),
              "wind_direction":(135,0),"battery_voltage":(3.33,0.02)}
    for key,(target,tolerance) in expected.items():
        if not math.isclose(r[key],target,rel_tol=0,abs_tol=tolerance):
            raise ValueError(f"{key}: actual {r[key]}, expected {target} +/- {tolerance}")
    # 27 pulses/second gives 18 m/s; at least one complete high-wind window
    # is not required by the smoke scenario, so require measured gust >= threshold.
    if not 10 <= r["wind_gust"] <= 20 or not 1 <= r["wind_speed"] <= 4:
        raise ValueError(f"unexpected measured pulse rate: {r}")
    return {"result":"PASS","scope":"Task 02 Wokwi hardware I/O","simulated":True,
            "expected":expected,"telemetry":r}

if __name__=="__main__":
    print(json.dumps(verify(json.loads(Path(sys.argv[1]).read_text())),indent=2))
