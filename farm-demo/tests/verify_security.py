"""Task 12 live integration: real JWT, native RBAC/API keys, HTTPS, MQTT/TLS."""
import argparse
import json
import os
import secrets
import socket
import ssl
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from verify_thingsboard import initial_setup, wait_login, STATION, MqttRestCloud, provision
from verify_rule_engine import eventually
from provision import Rest, ApiError
from security import install_security, activate, issue_key, ROLES, HUMAN_ROLES, uid
from farm_api import FarmApi, make_server
from sync import CloudUnavailable

def deny(fn, statuses=(401,403)):
    try:
        fn()
    except ApiError as exc:
        assert exc.status in statuses, str(exc)
        return exc.status
    raise AssertionError("unauthorized operation was accepted")

def reject_connection(fn):
    try:
        fn()
    except (OSError, CloudUnavailable):
        return
    raise AssertionError("insecure connection accepted")

def run(url, ca, mqtt_port, audit):
    initial_setup(url,ca)
    admin = wait_login(url,ca)
    topology, gateway_token = provision(admin)
    station = topology["devices"][STATION]
    path = f"/api/plugins/telemetry/DEVICE/{station}"
    # Independent instance: publish real telemetry before assigning ownership;
    # later scoped reads also prove the existing history survived provisioning.
    seed=MqttRestCloud("127.0.0.1",mqtt_port,url,topology["devices"],token=gateway_token,
        api_token=admin.authorization.removeprefix("Bearer "),ca_file=ca,timeout=10)
    try:
        seed.connect()
        seed_stamp=int(time.time()*1000)-1000
        seed_packet={STATION:[{"ts":seed_stamp,"values":{"record_id":f"{STATION}:1",
            "sequence_number":1,"status":0,"period_s":300,"valid_mask":127,
            "temperature":29.5,"humidity":78.2,"pressure":1008.4,"wind_speed":6.3,
            "wind_gust":8.8,"wind_direction":135,"rainfall":2.5146,"battery_voltage":3.2,
            "sync_mode":"live"}}]}
        seed.publish(seed_packet)
        eventually(lambda:seed.read(seed_packet).get((STATION,seed_stamp),{}).get("record_id")==f"{STATION}:1",
                   "seed real history")
    finally:
        seed.close()
    emails = {name:f"farm-ci-role-{name}@example.invalid" for name in ROLES}
    manifest, users, created = install_security(admin,topology,emails)
    assert all(created.values())
    assert install_security(admin,topology,emails)[0]==manifest
    clients={}
    for name,user in users.items():
        password="Aa1!"+secrets.token_hex(24)
        activate(admin,user,password)
        client=Rest(url,ca)
        client.login(emails[name],password)
        assert client.request("GET","/api/auth/user")["id"]["id"]==uid(user)
        clients[name]=client
    reader_key=issue_key(admin,users["reader"],"CI history reader")
    control_key=issue_key(admin,users["control"],"CI command service")
    reader=Rest(url,ca,authorization="ApiKey "+reader_key["value"])
    history=path+"/values/timeseries"
    # A distinct same-tenant device outside the managed farm demonstrates
    # that authorization is scoped to actual resource groups, not merely tenant.
    other=admin.request("POST","/api/device",{"name":"CI security outside farm","type":"default"})
    other_id=uid(other)
    outside=f"/api/plugins/telemetry/DEVICE/{other_id}/values/timeseries"
    report=[]
    def checked(label):
        report.append({"check":label,"result":"PASS"})
    for name in HUMAN_ROLES:
        user=clients[name]
        assert user.request("GET",history).get("record_id"),name
        deny(lambda:user.request("GET",outside),(401,403,404))
        deny(lambda:user.request("POST",path+"/SERVER_SCOPE",{"farm_state_ts":1}))
        deny(lambda:user.request("POST",path+"/SHARED_SCOPE",{"wind_alarm_on":99}))
        deny(lambda:user.request("POST",f"/api/rpc/oneway/{station}",
            {"method":"request_stop_irrigation","params":{},"persistent":True}))
        deny(lambda:user.request("GET",f"/api/device/{station}/credentials"))
        if name!="admin":
            deny(lambda:user.request("POST","/api/user",{"email":"forbidden@example.invalid",
                "authority":"CUSTOMER_USER","customerId":{"entityType":"CUSTOMER","id":manifest["customer"]}},
                sendActivationMail="false"))
        checked("native scoped read + direct write/RPC/credentials denied: "+name)
    assert clients["admin"].request("GET",f"/api/user/{uid(users['viewer'])}")["email"]==emails["viewer"]
    checked("admin native account management scope")
    assert reader.request("GET",history).get("record_id")
    deny(lambda:reader.request("GET",outside),(401,403,404))
    deny(lambda:reader.request("POST",path+"/SHARED_SCOPE",{"wind_alarm_on":99}))
    deny(lambda:reader.request("POST",f"/api/rpc/oneway/{station}",
        {"method":"test_buzzer","params":{},"persistent":True}))
    deny(lambda:reader.request("GET",f"/api/device/{station}/credentials"))
    checked("separate limited native API key")
    # Real MQTT/TLS adapter uses the restricted key, not tenant admin JWT.
    adapter=MqttRestCloud("127.0.0.1",mqtt_port,url,topology["devices"],
                          token=gateway_token,api_key=reader_key["value"],ca_file=ca,timeout=10)
    try:
        adapter.connect()
        stamp=int(time.time()*1000)
        packet={STATION:[{"ts":stamp,"values":{"record_id":f"{STATION}:20001",
            "sequence_number":20001,"status":1,"period_s":300,"valid_mask":127,
            "temperature":29.5,"humidity":78.2,"pressure":1008.4,"wind_speed":10.0,
            "wind_gust":10.0,"wind_direction":135,"rainfall":0.0,"battery_voltage":3.2,
            "sync_mode":"live"}}]}
        adapter.publish(packet)
        eventually(lambda:adapter.read(packet).get((STATION,stamp),{}).get("record_id")==f"{STATION}:20001",
                   "restricted reader did not verify real telemetry")
        active=eventually(lambda:[x for x in admin.request("GET",f"/api/alarm/DEVICE/{station}",
            pageSize=100,page=0,searchStatus="ACTIVE")["data"] if x["type"]=="WIND_HIGH"],"wind alarm not created")
        alarm_id=uid(active[0])
        checked("actual MQTT/TLS and readback with separate credentials")
        deny(lambda:clients["operator"].request("POST",f"/api/alarm/{alarm_id}/clear"))
        deny(lambda:clients["viewer"].request("POST",f"/api/alarm/{alarm_id}/ack"))
        # Exercise HTTPS service boundary using actual signed ThingsBoard JWTs.
        app=FarmApi(url,ca,manifest,control_key["value"],audit)
        tls=Path(ca).parent
        server=make_server(app,"127.0.0.1",0,str(tls/"server.crt"),str(tls/"server.key"))
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        endpoint=f"https://127.0.0.1:{server.server_port}"
        def call(name,action,body,target=station,authorization=None):
            auth=authorization if authorization is not None else clients[name].authorization
            return Rest(endpoint,ca,authorization=auth).request("POST",f"/api/farm/stations/{target}/{action}",body)
        try:
            req={"method":"request_stop_irrigation","params":{}}
            for name in HUMAN_ROLES:
                for action,body in [("export",{"startTs":stamp-1000,"endTs":stamp+1000}),
                                    ("ack",{"alarm_id":alarm_id})]:
                    if name=="viewer":
                        deny(lambda:call(name,action,body))
                    else:
                        result=call(name,action,body)
                        assert result.get("record_id" if action=="export" else "acknowledged")
                if name=="viewer":
                    deny(lambda:call(name,"rpc",req))
                else:
                    assert call(name,"rpc",req)["state"]=="PENDING"
                test={"method":"test_buzzer","params":{}}
                config={"method":"set_config","params":{"config_version":2,"wind_alarm_on":10,
                         "wind_alarm_off":7,"report_period_s":300}}
                for body in (test,config):
                    if name in ("admin","technician"):
                        assert call(name,"rpc",body)["state"]=="PENDING"
                    else:
                        deny(lambda:call(name,"rpc",body))
                deny(lambda:call(name,"rpc",{"method":"start_pump","params":{}}))
                deny(lambda:call(name,"rpc",req,target=other_id))
                checked("actual HTTPS business matrix: "+name)
            assert admin.request("GET","/api/alarm/"+alarm_id)["acknowledged"]
            assert not admin.request("GET","/api/alarm/"+alarm_id)["cleared"]
            deny(lambda:call("admin","rpc",{"method":"set_config","params":{"config_version":2,
                "wind_alarm_on":99,"wind_alarm_off":7,"report_period_s":300}}),(400,))
            deny(lambda:call("operator","rpc",req,authorization="Bearer invalid"))
            jwt=clients["operator"].authorization
            # Modify the first signature byte, not an unused base64 padding bit.
            parts=jwt.split(".");parts[-1]=("A" if parts[-1][0]!="A" else "B")+parts[-1][1:]
            deny(lambda:call("operator","rpc",req,authorization=".".join(parts)))
            deny(lambda:call("reader","rpc",req,authorization="ApiKey "+reader_key["value"]))
            # Revocation must affect a previously issued JWT at the business boundary.
            admin.request("POST",f"/api/entityGroup/{manifest['groups']['operator']}/deleteEntities",
                          [uid(users["operator"])])
            deny(lambda:call("operator","rpc",req))
            checked("JWT tamper, method allowlist, scope, group revocation; ack not clear")
            with sqlite3.connect(audit) as db:
                counts=dict(db.execute("SELECT outcome,COUNT(*) FROM audit GROUP BY outcome"))
                assert counts["DENIED"]>=10 and counts["ACCEPTED"]>=10
                assert db.execute("SELECT COUNT(*) FROM audit WHERE actor=? AND action=? AND outcome='ACCEPTED'",
                    (uid(users["viewer"]),"request_stop_irrigation")).fetchone()[0]==0
            checked("durable actor/action/outcome audit")
        finally:
            server.shutdown();server.server_close();thread.join(5)
    finally:
        adapter.close()
    bad=MqttRestCloud("127.0.0.1",mqtt_port,url,topology["devices"],token=secrets.token_hex(24),
                      api_key=reader_key["value"],ca_file=ca,timeout=3)
    try:
        reject_connection(bad.connect)
    finally:
        bad.close()
    reject_connection(lambda:Rest(url).request("GET","/api/auth/user"))
    for port in (mqtt_port,int(url.rsplit(":",1)[-1])):
        def wrong_hostname():
            context=ssl.create_default_context(cafile=ca)
            with socket.create_connection(("127.0.0.1",port),timeout=5) as sock:
                with context.wrap_socket(sock,server_hostname="not-localhost.example.invalid"):
                    pass
        reject_connection(wrong_hostname)
    checked("invalid MQTT token; unknown CA and wrong TLS hostname rejected")
    admin.request("PUT",f"/apiKey/{uid(reader_key)}/enabled/false")
    deny(lambda:reader.request("GET",history))
    checked("API key revoked")
    return {"result":"PASS","scope":"actual non-production ThingsBoard, JWT, native roles and API keys, MQTT/TLS and HTTPS; no physical LoRaWAN or firmware RPC execution claim",
            "checks":report,"audit_counts":counts}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--url",default="https://127.0.0.1:8443")
    p.add_argument("--ca",required=True)
    p.add_argument("--port",type=int,default=8883)
    p.add_argument("--audit",required=True)
    a=p.parse_args()
    os.umask(0o077)
    print(json.dumps(run(a.url,a.ca,a.port,a.audit),indent=2))
