"""Run alongside gateway.py; only one sync worker per database (Linux)."""
import argparse
import fcntl
import json
import logging
import os
import time
from pathlib import Path
from outbox import connect, radio_diagnostics
from cloud import MqttRestCloud
from sync import Synchronizer, CloudUnavailable
import rpc

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",required=True)
    parser.add_argument("--host",required=True)
    parser.add_argument("--port",type=int,default=8883)
    parser.add_argument("--url",required=True)
    parser.add_argument("--devices",required=True,help="JSON map station name to provisioned Cloud device UUID")
    parser.add_argument("--ca-file",help="Trusted CA for MQTT and HTTPS (defaults to system trust)")
    parser.add_argument("--demo-loopback",action="store_true")
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    devices=json.loads(Path(args.devices).read_text())
    def receive(raw):
        # Callback runs on the MQTT network thread, with its own connection.
        incoming=connect(args.db,recover=False)
        try:
            rpc.schema(incoming)
            rpc.admit(incoming,raw,devices)
        except (ValueError,KeyError,TypeError,AttributeError):
            logging.warning('Rejected malformed/scoped RPC')
        finally:
            incoming.close()
    cloud=MqttRestCloud(args.host,args.port,args.url,devices,
        token=os.getenv("FARM_CLOUD_TOKEN"),api_token=os.getenv("FARM_CLOUD_READ_TOKEN"),
        demo=args.demo_loopback, ca_file=args.ca_file, api_key=os.getenv("FARM_CLOUD_READ_API_KEY"),rpc_receiver=receive)
    with open(str(Path(args.db).resolve())+".sync.lock","a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db=connect(args.db)
        try:
            rpc.schema(db)
            worker=Synchronizer(db,cloud)
            previous=None
            next_health=0
            next_rpc=0
            while True:
                worker.step()
                health=worker.status()
                signature=(health["state"],health["pending"],health["near_full"])
                if signature!=previous:
                    logging.info("sync health=%s",json.dumps(health))
                    previous=signature
                now=worker.clock()
                if worker.connected and now>=next_rpc:
                    try:
                        rpc.flush(db,cloud,now)
                    except CloudUnavailable:
                        worker.offline(now)
                    next_rpc=now+1000
                if worker.connected and now>=next_health:
                    try:
                        health['radio_diagnostics']={devices[name]:value for name,value in radio_diagnostics(db).items() if name in devices}
                        cloud.publish_health(health,now)
                    except CloudUnavailable:
                        worker.offline(now)
                    next_health=now+30000
                time.sleep(.25)
        finally:
            cloud.close()
            db.close()

if __name__=="__main__":
    main()
