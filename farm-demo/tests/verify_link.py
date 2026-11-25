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
    req("SIM_RTC offset=0 trusted=1" in log,"untrusted simulator clock")
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
    tx=re.findall(r"RADIO_TX seq=(\d+) fcnt=(\d+) fport=(\d+) hex=([0-9a-f]+) delivered=([01]) ts=(\d+)",log)
    req(tx and [int(t[1]) for t in tx]==list(range(1,len(tx)+1)),"frame counter reuse")
    for seq,_,port,payload,_,_ in tx:
        req(generated[int(seq)]==(int(port),payload),"replay changed bytes")
    delivered={int(t[0]) for t in tx if t[4]=="1"}
    req(set(stored)<=delivered,"missing recovered uplink")
    req(any(t[4]=="0" for t in tx),"no dropped uplink")
    req("DOWNLINK" not in log[up:auto],"ACK was not dropped")
    # Observe the actual FPort 20 bytes and match every release with the
    # simulator's preceding delivered uplink and legal Class A window.
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'edge'))
    from weather_pb2 import WeatherAck
    pending=set()
    latest=None
    released=[]
    for line in log.splitlines():
        m=re.search(r"BUFFER_STORED (\d+) pending=(\d+)",line)
        if m:
            pending.add(int(m[1]))
            req(len(pending)==int(m[2]),"wrong enqueue depth")
        m=re.search(r"RADIO_TX seq=(\d+) fcnt=(\d+) fport=(\d+) hex=([0-9a-f]+) delivered=([01]) ts=(\d+)",line)
        if m:
            latest=m
            req(int(m[1]) in pending,"transmitted released/unknown record")
        m=re.search(r"ACK_FRAME ts=(\d+) window=(\d+) fport=(\d+) hex=([0-9a-f]+)",line)
        if m:
            ack=WeatherAck.FromString(bytes.fromhex(m[4]))
            req(latest is not None and latest[5]=="1" and int(m[3])==20,"ACK without delivered uplink")
            req(int(m[2]) in (1,2) and int(m[1])-int(latest[6])==int(m[2]),"ACK outside window")
            req(list(ack.sequence_number)==[int(latest[1])],"wrong ACK identity")
            req(int(latest[1]) in pending,"duplicate release")
            pending.remove(int(latest[1]));released.append(int(latest[1]))
        m=re.search(r"DOWNLINK fport=20 rx=accepted count=1 pending=(\d+)",line)
        if m:
            req(len(pending)==int(m[1]),"wrong ACK depth")
    req(not pending and set(stored)<=set(released),"queue not drained")
    req(any(sum(t[0]==str(seq) for t in tx)>1 for seq in stored),"no retry")
    return dict(result="PASS",scope="Wokwi logical Class A and simulated gateway ACK, not RF/SQLite",offline_sequences=stored,transmissions=len(tx),recovered=sorted(delivered))
if __name__=="__main__":
    print(json.dumps(verify(Path(sys.argv[1]).read_text()),indent=2))
