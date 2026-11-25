"""Cloud-side stale-data tick. No weather values or decisions originate here."""
import argparse
import os
import time
import ssl
import threading
import json
import paho.mqtt.client as mqtt

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host",required=True)
    p.add_argument("--port",type=int,default=8883)
    p.add_argument("--ca-file")
    p.add_argument("--interval",type=int,default=30)
    a=p.parse_args()
    if not 1 <= a.interval <= 60:
        raise ValueError("watchdog interval must be 1–60 seconds")
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,protocol=mqtt.MQTTv311)
    client.username_pw_set(os.environ["FARM_WATCHDOG_STATION_TOKEN"])
    client.tls_set_context(ssl.create_default_context(cafile=a.ca_file))
    ready=threading.Event()
    client.on_connect=lambda c,u,f,r,p: ready.set() if not r.is_failure else None
    client.on_disconnect=lambda *args: ready.clear()
    client.connect_async(a.host,a.port,keepalive=30)
    client.loop_start()
    try:
        while True:
            if ready.wait(10):
                info=client.publish("v1/devices/me/telemetry",
                    json.dumps({"ts":int(time.time()*1000),"values":{"farm_watchdog":True}}),qos=1)
                try:
                    info.wait_for_publish(timeout=10)
                except RuntimeError:
                    pass
            time.sleep(a.interval)
    finally:
        client.disconnect()
        client.loop_stop()

if __name__=="__main__":
    main()
