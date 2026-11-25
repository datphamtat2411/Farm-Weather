"""Task 13 integration helper executed inside the actual security CI job."""
import base64
import datetime as dt
import json
import subprocess
import tempfile
import time
from pathlib import Path
from outbox import connect
import rpc
from gateway import handle
from verify_rule_engine import eventually
from weather_pb2 import FarmCommand

ROOT=Path(__file__).resolve().parents[1]
APP,DEV,STATION="farm-demo","0000000000000001","FARM_WS_01"
MAPPING={APP+"/device/"+DEV:STATION}

def exercise(submit,cloud,path,user,station):
    with tempfile.TemporaryDirectory() as tmp:
        probe=Path(tmp)/"rpc-probe"
        subprocess.run(["g++","-std=c++17","-Wall","-Wextra","-Werror",
            "-I",str(ROOT/"firmware/include"),str(ROOT/"tests/rpc_probe.cpp"),"-o",str(probe)],check=True)
        with connect(path,recover=False) as db:
            rpc.schema(db)
            seq=30000
            for method,params,expected,safety in [
                ("test_buzzer",{},"APPLIED","safe"),
                ("request_stop_irrigation",{},"APPLIED","safe"),
                ("set_config",{"config_version":2,"wind_alarm_on":9,"wind_alarm_off":6,"report_period_s":300},"REJECTED","unsafe"),
                ("set_config",{"config_version":3,"wind_alarm_on":9,"wind_alarm_off":6,"report_period_s":300},"APPLIED","safe")]:
                ident=submit(method,params)["command_id"]
                def row():
                    return db.execute("SELECT state FROM farm_commands WHERE command_id=?",(ident,)).fetchone()
                assert eventually(row,"native Gateway RPC not received")[0]=="PENDING"
                topic,down=rpc.next_downlink(db,STATION,APP,DEV,seq,now=rpc.now_ms())
                command=base64.b64decode(down["data"])
                decoded=FarmCommand.FromString(command)
                assert str(__import__("uuid").UUID(bytes=decoded.command_id))==ident
                assert row()[0]=="QUEUED"
                # No optimistic applied state before the native firmware runs.
                seq+=1
                stamp=max(decoded.created_at_s,int(time.time()))
                wire=bytes.fromhex(subprocess.check_output([str(probe),command.hex(),str(stamp),str(seq),safety],text=True).strip())
                response=dict(deviceInfo=dict(applicationId=APP,devEui=DEV),fPort=22,fCnt=seq,
                    data=base64.b64encode(wire).decode(),time=dt.datetime.fromtimestamp(stamp,dt.timezone.utc).isoformat(),rxInfo=[])
                outcome,ack=handle(db,MAPPING,f"application/{APP}/device/{DEV}/event/up",json.dumps(response).encode())
                assert outcome=="inserted" and ack and row()[0]==expected
                key="command_"+ident.replace("-","")
                def synced():
                    rpc.flush(db,cloud,rpc.now_ms())
                    return db.execute("SELECT COUNT(*) FROM command_events WHERE command_id=? AND published=0",(ident,)).fetchone()[0]==0
                eventually(synced,"command lifecycle not persisted/read back",seconds=35)
                data=user.request("GET",f"/api/plugins/telemetry/DEVICE/{station}/values/timeseries",keys=key)
                assert json.loads(data[key][0]["value"])["state"]==expected
                if method=="set_config" and expected=="APPLIED":
                    def confirmed():
                        attrs=user.request("GET",f"/api/plugins/telemetry/DEVICE/{station}/values/attributes/CLIENT_SCOPE")
                        values={a["key"]:a["value"] for a in attrs}
                        return values.get("applied_config_version")==3 and values.get("applied_wind_alarm_on")==9
                    eventually(confirmed,"client applied configuration absent")
            db.close()
