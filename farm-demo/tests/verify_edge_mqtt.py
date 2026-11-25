"""Real Mosquitto + Edge process + native firmware bytes + simulated UART RX."""
import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
import paho.mqtt.client as mqtt

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"edge"))
from serial_bridge import SerialBridge
from protocol import decode
from weather_pb2 import WeatherRecord

APP, DEV="farm-demo","0000000000000001"

def require(ok, reason):
    if not ok:
        raise AssertionError(reason)

def wait_until(predicate, reason, timeout=10):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError(reason)

def run(native_log, db_path):
    require(not db_path.exists(),"fresh evidence database required")
    vectors={}
    for line in native_log.read_text().splitlines():
        name,port,wire=line.split()
        vectors[name]=(int(port),wire)
    db_path=db_path.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0))
            port=sock.getsockname()[1]
        config=tmp/"mqtt.conf"
        config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
        broker=subprocess.Popen(["mosquitto","-c",str(config)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        edge=None
        log_path=tmp/"edge.log"
        log=log_path.open("w")
        client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        ready=threading.Event()
        acked=threading.Event()
        lock=threading.Lock()
        outcomes=[]
        def start_edge():
            child=subprocess.Popen([sys.executable,str(ROOT/"edge/gateway.py"),
                "--mapping",str(ROOT/"edge/devices.example.json"),"--db",str(db_path),
                "--port",str(port)],stdout=log,stderr=log)
            return child
        def stop_edge():
            if edge and edge.poll() is None:
                edge.terminate()
                edge.wait(timeout=10)
        def can_connect():
            try:
                with socket.create_connection(("127.0.0.1",port),timeout=.2):
                    return True
            except OSError:
                return False
        def publish(topic, body):
            require(client.publish(topic,body,qos=1).rc==mqtt.MQTT_ERR_SUCCESS,"uplink not queued")
        bridge=SerialBridge(APP,DEV,publish)
        def connected(c,u,flags,reason,props):
            if not reason.is_failure:
                c.subscribe(bridge.down_topic,qos=1)
        def subscribed(c,u,mid,reasons,props):
            if not any(r.is_failure for r in reasons):
                ready.set()
        def down(c,u,message):
            with lock:
                bridge.on_downlink(message.topic,message.payload)
                if bridge.ack:
                    with sqlite3.connect(db_path) as reader:
                        row=reader.execute("SELECT payload_json,state FROM weather_outbox WHERE device_id=? AND sequence_number=?",
                                           ("FARM_WS_01",bridge.active)).fetchone()
                    outcomes.append((bridge.active,row))
                    acked.set()
        def edge_ready(count):
            return log_path.read_text().count("EDGE_SUBSCRIBED")>=count
        def processed(outcome,count):
            return log_path.read_text().count("uplink="+outcome+" ")>=count
        def tx(name,fcnt,delivered=1,wire=None):
            p,h=vectors[name]
            h=h if wire is None else wire
            record=decode(p,bytes.fromhex(h),"FARM_WS_01")
            seq=record["sequence_number"]
            acked.clear()
            with lock:
                result=bridge.on_line(f"RADIO_TX seq={seq} fcnt={fcnt} fport={p} hex={h} delivered={delivered} ts=10")
            require(result is None,"unexpected immediate ACK")
            return record
        def finish_ack(expected):
            require(acked.wait(10),"no matching MQTT application ACK")
            with lock:
                command=bridge.on_line("RX1")
            require(command and command.startswith("DOWNLINK 20 "),"missing RX1 downlink")
            require(outcomes[-1][0]==expected["sequence_number"] and outcomes[-1][1] is not None,
                    "ACK before committed row")
            require(json.loads(outcomes[-1][1][0])==expected and outcomes[-1][1][1]=="PENDING","committed payload mismatch")
            return command
        try:
            wait_until(can_connect,"Mosquitto not ready")
            edge=start_edge()
            wait_until(lambda:edge_ready(1),"Edge subscription not ready")
            client.on_connect,client.on_subscribe,client.on_message=connected,subscribed,down
            client.connect("127.0.0.1",port)
            client.loop_start()
            require(ready.wait(10),"bridge MQTT subscription not ready")
            require(bridge.on_line("BOOT FARM_WS_01")=="ACK MANUAL","manual mode handshake")
            bridge.on_line("ACK_MANUAL")
            bridge.on_line("SIM_RTC offset=0 trusted=1")
            periodic=tx("report",1)
            first=finish_ack(periodic)
            alarm=tx("event",2)
            finish_ack(alarm)
            # Restart service; duplicate frame counter differs but record identity is stable.
            stop_edge()
            edge=start_edge()
            wait_until(lambda:edge_ready(2),"restarted Edge subscription not ready")
            duplicate=tx("report",3)
            require(finish_ack(duplicate)==first,"replay ACK changed")
            # No uplink for dropped radio; no downlink for conflicting data.
            tx("report",4,delivered=0)
            require(bridge.on_line("RX1") is None and bridge.on_line("RX2") is None,"ACK for dropped frame")
            changed=WeatherRecord.FromString(bytes.fromhex(vectors["report"][1]))
            changed.rain_tips+=1
            tx("report",5,wire=changed.SerializeToString().hex())
            wait_until(lambda:processed("conflict",1),"conflict not quarantined")
            with lock:
                require(bridge.on_line("RX1") is None and bridge.on_line("RX2") is None,"conflict ACKed")
            bridge.on_line("SIM_RTC offset=0 trusted=0")
            tx("report",6)
            wait_until(lambda:processed("invalid",1),"untrusted clock not quarantined")
            with lock:
                require(bridge.on_line("RX2") is None,"invalid clock ACKed")
            with sqlite3.connect(db_path) as reader:
                rows=reader.execute("SELECT kind,sequence_number,payload_json,state FROM weather_outbox ORDER BY sequence_number").fetchall()
                quarantine=reader.execute("SELECT reason,occurrences FROM quarantine ORDER BY id").fetchall()
                require(reader.execute("PRAGMA integrity_check").fetchone()==("ok",),"SQLite integrity failed")
            require(len(rows)==2 and len(quarantine)==2 and len(outcomes)==3,"wrong record/ACK counts")
            require(all(r[3]=="PENDING" for r in rows),"unexpected sync state")
            return dict(result="PASS",scope="real MQTT broker and Edge service, native firmware wire vectors, logical RX adapter (not Wokwi/RF)",
                        unique_records=len(rows),ack_count=len(outcomes),quarantined=len(quarantine),
                        records=[json.loads(r[2]) for r in rows],quarantine_reasons=[r[0] for r in quarantine],
                        replay_after_restart=True,ack_after_commit=True)
        finally:
            client.disconnect()
            client.loop_stop()
            stop_edge()
            broker.terminate()
            broker.wait(timeout=10)
            log.close()
            print(log_path.read_text(),file=sys.stderr,end="")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("native_log",type=Path)
    parser.add_argument("db",type=Path)
    args=parser.parse_args()
    print(json.dumps(run(args.native_log,args.db),indent=2,allow_nan=False))
