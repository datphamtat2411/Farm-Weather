"""Durable, station-scoped RPC lifecycle. MQTT delivery is not application."""
import base64
import json
import logging
import sqlite3
import time
import uuid

from weather_pb2 import FarmCommand, CommandResult, WeatherAck
from google.protobuf.message import DecodeError
from outbox import canonical, now_ms

LOG = logging.getLogger("farm.rpc")
METHODS = {"test_buzzer":1,"request_stop_irrigation":2,"set_config":3}
RESULTS = {1:"APPLIED",2:"REJECTED",3:"EXPIRED",4:"UNKNOWN"}
TERMINAL = frozenset(RESULTS.values())
CONFIG = ("config_version","wind_alarm_on","wind_alarm_off","report_period_s")

def require(ok, reason):
    if not ok:
        raise ValueError(reason)

def schema(db):
    with db:
        db.execute("""CREATE TABLE IF NOT EXISTS farm_commands (
            command_id TEXT PRIMARY KEY, station TEXT NOT NULL, rpc_id INTEGER NOT NULL,
            method TEXT NOT NULL, params TEXT NOT NULL, created INTEGER NOT NULL,
            expires INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING',
            dispatched INTEGER NOT NULL DEFAULT 0, last_send INTEGER NOT NULL DEFAULT 0,
            result TEXT, revision INTEGER NOT NULL DEFAULT 1, published INTEGER NOT NULL DEFAULT 0,
            stamp INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS command_receipts (
            station TEXT NOT NULL, sequence INTEGER NOT NULL, command_id TEXT NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY(station,sequence))""")
        db.execute("CREATE INDEX IF NOT EXISTS command_pending ON farm_commands(station,state,created)")
        db.execute("""CREATE TABLE IF NOT EXISTS command_events (
            command_id TEXT NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL,
            result TEXT, stamp INTEGER NOT NULL, published INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(command_id,revision))""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS command_admission AFTER INSERT ON farm_commands
            BEGIN INSERT INTO command_events(command_id,revision,state,result,stamp)
                VALUES(new.command_id,new.revision,new.state,new.result,new.stamp); END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS command_transition AFTER UPDATE OF revision ON farm_commands
            WHEN new.revision>old.revision
            BEGIN INSERT INTO command_events(command_id,revision,state,result,stamp)
                VALUES(new.command_id,new.revision,new.state,new.result,new.stamp); END""")

def envelope(raw, stations):
    require(len(raw)<=8192,"oversize RPC")
    body=json.loads(raw)
    require(isinstance(body,dict) and set(body)=={"device","data"},"invalid RPC envelope")
    station=body["device"]
    require(isinstance(station,str) and station in stations,"unmapped RPC station")
    data=body["data"]
    require(isinstance(data,dict),"invalid RPC data")
    rpc_id,method,p=data["id"],data["method"],data["params"]
    require(type(rpc_id) is int and 0<=rpc_id<=2147483647 and isinstance(method,str) and method in METHODS,
            "invalid RPC method/id")
    require(isinstance(p,dict) and set(p)==set(("command_id","created_at_ms","expires_at_ms")) |
            (set(CONFIG) if method=="set_config" else set()),"invalid parameters")
    ident=str(uuid.UUID(p["command_id"]))
    require(ident==p["command_id"] and uuid.UUID(ident).int!=0,"invalid command id")
    created,expires=p["created_at_ms"],p["expires_at_ms"]
    require(type(created) is int and type(expires) is int and 0<created<expires<=4294967295000
            and expires-created<=900000,"invalid expiration")
    if method=="set_config":
        require(all(type(p[k]) is int for k in CONFIG) and 0<p["config_version"]<=2147483647
                and 0<p["wind_alarm_off"]<p["wind_alarm_on"]<=10 and p["report_period_s"] in (300,900),
                "unsafe config")
    return station,rpc_id,method,p

def admit(db, raw, stations, now=None):
    now=now_ms() if now is None else now
    station,rpc_id,method,p=envelope(raw,stations)
    require(p["created_at_ms"]<=now+1000,"future command")
    ident=p["command_id"]
    old=db.execute("SELECT station,method,params FROM farm_commands WHERE command_id=?",(ident,)).fetchone()
    content=canonical(p)
    if old:
        require(old==(station,method,content),"command identity conflict")
        # ThingsBoard may allocate a new native delivery id after reconnect.
        with db: db.execute("UPDATE farm_commands SET rpc_id=? WHERE command_id=?",(rpc_id,ident))
        return ident
    with db:
        db.execute("""INSERT INTO farm_commands(command_id,station,rpc_id,method,params,created,expires,state,stamp)
            VALUES(?,?,?,?,?,?,?,?,?)""",(ident,station,rpc_id,method,content,p["created_at_ms"],
            p["expires_at_ms"],"EXPIRED" if now>=p["expires_at_ms"] else "PENDING",now))
    return ident

def expire(db, now):
    with db:
        db.execute("""UPDATE farm_commands SET state=CASE WHEN dispatched=1 THEN 'UNKNOWN' ELSE 'EXPIRED' END,
            revision=revision+1,stamp=max(stamp+1,?) WHERE expires<=? AND state IN ('PENDING','QUEUED')""",(now,now))

def next_downlink(db, station, app, dev, ack_sequence, *, now=None, logical_ts=None):
    now=now_ms() if now is None else now
    expire(db,now)
    row=db.execute("""SELECT command_id,method,params,expires FROM farm_commands WHERE station=?
        AND state IN ('PENDING','QUEUED') AND expires>? AND last_send<=?
        ORDER BY created,command_id LIMIT 1""",(station,now,now-5000)).fetchone()
    if not row:
        return None
    ident,method,raw,expires=row
    p=json.loads(raw)
    # Only the Wokwi transport has an artificial epoch and accelerated clock.
    # Translate remaining real TTL at dispatch, never extend an expired command.
    created=p["created_at_ms"]//1000
    expiry=expires//1000
    if logical_ts is not None:
        created=logical_ts//1000
        expiry=created+(expires-now)//1000
    if expiry<=created:
        return None
    msg=FarmCommand(command_id=uuid.UUID(ident).bytes,created_at_s=created,
        expires_at_s=expiry,method=METHODS[method],ack_sequence=ack_sequence)
    if method=="set_config":
        for k in CONFIG: setattr(msg,k,p[k])
    wire=msg.SerializeToString(deterministic=True)
    require(len(wire)<=51,"command exceeds demo payload")
    with db:
        db.execute("""UPDATE farm_commands SET dispatched=1,last_send=?,state='QUEUED',
            revision=revision+CASE WHEN state='PENDING' THEN 1 ELSE 0 END,
            stamp=CASE WHEN state='PENDING' THEN max(stamp+1,?) ELSE stamp END WHERE command_id=?""",(now,now,ident))
    return f"application/{app}/device/{dev}/command/down",dict(devEui=dev,confirmed=False,fPort=21,
        data=base64.b64encode(wire).decode("ascii"))

def decode_result(payload):
    require(0<len(payload)<=51,"bad result length")
    try: m=CommandResult.FromString(payload)
    except DecodeError as exc: raise ValueError("bad result protobuf") from exc
    require(m.IsInitialized(),"missing result fields")
    require(m.SerializeToString(deterministic=True)==payload,"noncanonical result")
    m.DiscardUnknownFields()
    require(m.SerializeToString(deterministic=True)==payload,"unknown result field")
    require(m.sequence_number>0 and m.ts_s>0 and len(m.command_id)==16 and m.outcome in RESULTS
            and m.reason<=6 and (m.outcome==1)==(m.reason==0)
            and 0<m.config_version<=2147483647 and 0<m.wind_alarm_off<m.wind_alarm_on<=10
            and m.report_period_s in (300,900) and m.applied_period_s in (300,900)
            and m.outputs<=3,"invalid result values")
    require(m.applied_period_s>=m.report_period_s,"invalid applied period")
    return dict(command_id=str(uuid.UUID(bytes=m.command_id)),sequence=m.sequence_number,
        ts_s=m.ts_s,state=RESULTS[m.outcome],reason=m.reason,config_version=m.config_version,
        wind_alarm_on=m.wind_alarm_on,wind_alarm_off=m.wind_alarm_off,report_period_s=m.report_period_s,
        applied_period_s=m.applied_period_s,buzzer=bool(m.outputs&1),stop_request=bool(m.outputs&2))

def record_result(db, station, result, now):
    ident=result["command_id"];seq=result["sequence"];content=canonical(result)
    db.execute("BEGIN IMMEDIATE")
    try:
        row=db.execute("SELECT station,method,params,state,result,dispatched FROM farm_commands WHERE command_id=?",
                       (ident,)).fetchone()
        require(row and row[0]==station and row[5],"unsolicited or wrong-station command result")
        prior=db.execute("SELECT payload FROM command_receipts WHERE station=? AND sequence=?",(station,seq)).fetchone()
        require(not db.execute("SELECT 1 FROM weather_outbox WHERE device_id=? AND sequence_number=?",(station,seq)).fetchone(),
                "sequence collision with weather")
        if prior:
            require(prior[0]==content,"conflicting result sequence")
            db.commit();return "duplicate"
        if result["state"]=="APPLIED":
            if row[1]=="set_config":
                p=json.loads(row[2])
                require(all(result[k]==p[k] for k in CONFIG),"applied configuration mismatch")
            if row[1]=="test_buzzer": require(result["buzzer"],"buzzer not confirmed")
            if row[1]=="request_stop_irrigation": require(result["stop_request"],"stop not confirmed")
        db.execute("INSERT INTO command_receipts VALUES(?,?,?,?)",(station,seq,ident,content))
        # A replay response must not downgrade a verified terminal result.
        if row[3] not in ("APPLIED","REJECTED","EXPIRED") and not (row[4] and result["state"]=="UNKNOWN"):
            db.execute("""UPDATE farm_commands SET state=?,result=?,revision=revision+1,stamp=max(stamp+1,?)
                WHERE command_id=?""",(result["state"],content,now,ident))
        db.commit();return "inserted"
    except BaseException:
        db.rollback();raise

def flush(db, cloud, now):
    """Report status on a dedicated telemetry namespace; verify actual history.
    Client attributes derive only from the most recent confirmed config version."""
    expire(db,now)
    rows=db.execute("""SELECT e.command_id,c.station,c.rpc_id,c.method,e.state,e.result,e.revision,e.stamp
        FROM command_events e JOIN farm_commands c USING(command_id)
        WHERE e.published=0 ORDER BY e.stamp,e.command_id LIMIT 20""").fetchall()
    for ident,station,rpc_id,method,state,raw,rev,stamp in rows:
        result=json.loads(raw) if raw else None
        status={"command_id":ident,"method":method,"state":state,"revision":rev,"result":result}
        key="command_"+ident.replace("-","")
        value=canonical(status)
        packet={station:[{"ts":stamp,"values":{"farm_command":True,key:value}}]}
        cloud.publish(packet)
        # Readback uses the scoped Task 12 reader; marker is intentionally
        # stripped by the dedicated Rule Engine route.
        check={station:[{"ts":stamp,"values":{key:value}}]}
        if cloud.read(check).get((station,stamp),{}).get(key)!=value:
            continue
        if state=="APPLIED" and result:
            newest=db.execute("""SELECT result FROM farm_commands WHERE station=? AND state='APPLIED'
                AND method='set_config' AND result IS NOT NULL""",(station,)).fetchall()
            versions=[json.loads(x[0])["config_version"] for x in newest]
            if not versions or result["config_version"]>=max(versions):
                cloud.send("v1/gateway/attributes",{station:{
                    "applied_config_version":result["config_version"],"applied_period_s":result["applied_period_s"],
                    "applied_wind_alarm_on":result["wind_alarm_on"],"applied_wind_alarm_off":result["wind_alarm_off"],
                    "applied_report_period_s":result["report_period_s"]}})
        # Native RPC reply is best-effort transport status, not source of truth.
        cloud.send("v1/gateway/rpc",{"device":station,"id":rpc_id,"data":status})
        with db:
            db.execute("UPDATE command_events SET published=1 WHERE command_id=? AND revision=?",(ident,rev))
