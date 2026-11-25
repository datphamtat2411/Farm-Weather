"""Generate private Mosquitto password/ACL configuration for the demo LAN."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import subprocess

def generate(directory, *, app, dev, edge_password, sim_password, port=1883, bind="127.0.0.1"):
    if not re.fullmatch(r"[A-Za-z0-9_-]+",app) or not re.fullmatch(r"[0-9a-f]{16}",dev):
        raise ValueError("invalid app or DevEUI")
    if not 1 <= port <= 65535 or not ipaddress.ip_address(bind).is_private:
        raise ValueError("private listener required")
    for password in (edge_password,sim_password):
        if not isinstance(password,str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,128}",password):
            raise ValueError("use randomly generated password (24+ URL-safe chars)")
    if edge_password == sim_password:
        raise ValueError("separate MQTT passwords required")
    directory=Path(directory).resolve()
    if any(c.isspace() for c in str(directory)):
        raise ValueError("Mosquitto configuration path must not contain whitespace")
    directory.mkdir(mode=0o700,exist_ok=False)
    pw=directory/"passwords"
    fd=os.open(pw,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,"w") as out:
            out.write(f"farm-edge:{edge_password}\nfarm-simulator:{sim_password}\n")
        subprocess.run(["mosquitto_passwd","-U",str(pw)],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    except BaseException:
        pw.unlink(missing_ok=True)
        raise
    acl=directory/"acl"
    acl.write_text(f"user farm-edge\ntopic read application/{app}/device/+/event/up\n"
                   f"topic write application/{app}/device/{dev}/command/down\n"
                   f"user farm-simulator\ntopic write application/{app}/device/{dev}/event/up\n"
                   f"topic read application/{app}/device/{dev}/command/down\n")
    conf=directory/"mosquitto.conf"
    conf.write_text(f"listener {port} {bind}\nallow_anonymous false\npassword_file {pw}\nacl_file {acl}\n"
                    "persistence true\n"+f"persistence_location {directory}/\n")
    for file in (pw,acl,conf):
        file.chmod(0o600)
    return conf

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory")
    p.add_argument("--app",default="farm-demo")
    p.add_argument("--dev",default="0000000000000001")
    p.add_argument("--port",type=int,default=1883)
    p.add_argument("--bind",default="127.0.0.1")
    a=p.parse_args()
    generate(a.directory,app=a.app,dev=a.dev,port=a.port,bind=a.bind,
             edge_password=os.environ["EDGE_MQTT_PASSWORD"],sim_password=os.environ["SIM_MQTT_PASSWORD"])
