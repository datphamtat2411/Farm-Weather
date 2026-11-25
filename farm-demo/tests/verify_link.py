"""D05 evidence: actual serial TX bytes, exact replay, and application ACK."""
import json
import re
import sys
from pathlib import Path

def verify(log):
    def req(ok,msg):
        if not ok: raise ValueError(msg)
    req("HARDWARE_ERROR" not in log and "BUFFER_GAP" not in log,"storage failure")
    req("FLASH_READY highwater=0 pending=0 corrupt=0" in log,"unexpected initial storage")
    down=log.index("LINK_DOWN")
    up=log.index("LINK_UP")
    auto=log.index("ACK_AUTO")
    req(down<up<auto and "WIND_ALARM_ON" in log[down:up],"missing outage/local alarm")
    generated={}
    for obj in re.findall(r"(?:EVENT|TELEMETRY) (\{[^{}\r\n]*\})",log):
        row=json.loads(obj)
        generated[row["sequence_number"]]=(row["fport"],row["wire_hex"])
    stored=[int(x) for x in re.findall(r"BUFFER_STORED (\d+) pending=\d+",log[down:up])]
    req(len(stored)>=2 and len(set(stored))==len(stored),"missing offline records")
    tx=re.findall(r"RADIO_TX seq=(\d+) fcnt=(\d+) fport=(\d+) hex=([0-9a-f]+) delivered=([01])",log)
    req(tx and [int(t[1]) for t in tx]==list(range(1,len(tx)+1)),"frame counter reuse")
    for seq,_,port,payload,_ in tx:
        req(generated[int(seq)]==(int(port),payload),"replay changed bytes")
    delivered={int(t[0]) for t in tx if t[4]=="1"}
    req(set(stored)<=delivered,"missing recovered uplink")
    req(any(t[4]=="0" for t in tx),"no dropped uplink")
    req("DOWNLINK" not in log[up:auto],"ACK was not dropped")
    req("DOWNLINK fport=20 rx=accepted" in log[auto:] and "pending=0" in log[auto:],"queue not drained")
    req(any(sum(t[0]==str(seq) for t in tx)>1 for seq in stored),"no retry")
    return dict(result="PASS",scope="Wokwi logical Class A and simulated gateway ACK, not RF/SQLite",offline_sequences=stored,transmissions=len(tx),recovered=sorted(delivered))
if __name__=="__main__":
    print(json.dumps(verify(Path(sys.argv[1]).read_text()),indent=2))
