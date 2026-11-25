"""Farm business authorization boundary. Native ThingsBoard authenticates JWTs.

Human groups intentionally cannot call raw RPC/write attributes/clear alarms.
Only this narrowly scoped service identity may submit permitted operations.
Task 13 implements Edge/STM32 command execution and applied-state confirmation.
"""
import argparse
import json
import os
import re
import sqlite3
import ssl
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from provision import Rest, ApiError

CAPABILITIES = {
    "admin": frozenset(("export", "ack", "test_buzzer", "request_stop_irrigation", "set_config")),
    "technician": frozenset(("export", "ack", "test_buzzer", "request_stop_irrigation", "set_config")),
    "operator": frozenset(("export", "ack", "request_stop_irrigation")),
    "viewer": frozenset(),
}
HISTORY_KEYS = "record_id,sequence_number,temperature,humidity,pressure,wind_speed,wind_gust,wind_direction,rainfall,battery_voltage,period_s,status,valid_mask"
ROUTE = re.compile(r"/api/farm/stations/([0-9a-f-]{36})/(export|ack|rpc)$")

class Denied(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

def require(condition, status, message):
    if not condition:
        raise Denied(status, message)

def command_params(method, params):
    require(isinstance(params, dict), 400, "params must be an object")
    if method in ("test_buzzer", "request_stop_irrigation"):
        require(not params, 400, "unexpected command parameters")
    elif method == "set_config":
        require(set(params) == {"config_version", "wind_alarm_on", "wind_alarm_off", "report_period_s"},
                400, "invalid configuration fields")
        require(all(type(v) is int for v in params.values()), 400, "configuration requires integers")
        require(1 <= params["config_version"] <= 2147483647
                and 0 < params["wind_alarm_off"] < params["wind_alarm_on"] <= 10
                and params["report_period_s"] in (300,900), 400, "unsafe configuration")
    else:
        raise Denied(403, "method not allowed")
    return params

class FarmApi:
    def __init__(self, url, ca_file, manifest, control_key, audit_path):
        if not control_key:
            raise ValueError("control API key required")
        self.url, self.ca_file, self.manifest = url, ca_file, manifest
        self.service = Rest(url, ca_file, authorization="ApiKey "+control_key)
        self.audit_path = audit_path
        with sqlite3.connect(audit_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY, ts_ms INTEGER NOT NULL, actor TEXT NOT NULL,
                station TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL,
                outcome TEXT NOT NULL)""")

    def audit(self, actor, station, action, detail, outcome):
        with sqlite3.connect(self.audit_path) as db:
            db.execute("INSERT INTO audit(ts_ms,actor,station,action,detail,outcome) VALUES (?,?,?,?,?,?)",
                       (int(time.time()*1000), actor, station, action, json.dumps(detail), outcome))

    def principal(self, authorization, station):
        require(isinstance(authorization, str) and authorization.startswith("Bearer ")
                and 0 < len(authorization) < 8192, 401, "JWT required")
        user_api = Rest(self.url, self.ca_file, authorization=authorization)
        try:
            user = user_api.request("GET", "/api/auth/user")
        except ApiError as e:
            if e.status in (401,403):
                raise Denied(401, "invalid or expired JWT") from None
            raise
        actor = user["id"]["id"]
        require(user["authority"] == "CUSTOMER_USER"
                and user["customerId"]["id"] == self.manifest["customer"], 403, "farm scope denied")
        # Resolve current group membership for every request; never trust a
        # role/identity supplied by the browser or decode an unverified JWT.
        memberships = self.service.request("GET", "/api/entityGroups/USER/"+actor)
        ids = {g["id"] for g in memberships}
        roles = [r for r in CAPABILITIES if self.manifest["groups"][r] in ids]
        require(len(roles)==1, 403, "no unambiguous farm role")
        require(station in self.manifest["station_ids"], 403, "station scope denied")
        require(user_api.request("GET", f"/api/permission/DEVICE/{station}/READ") is True,
                403, "station permission denied")
        return user_api, actor, roles[0]

    def execute(self, authorization, station, action, body):
        actor = "unverified"
        operation = action
        detail = {}
        try:
            user_api, actor, role = self.principal(authorization, station)
            require(isinstance(body, dict), 400, "object required")
            if action == "rpc":
                require(set(body)=={"method","params"}, 400, "invalid command fields")
                operation = body["method"]
                require(isinstance(operation,str) and operation in CAPABILITIES[role],
                        403, "operation denied")
                require(operation in ("test_buzzer","request_stop_irrigation","set_config"),
                        403, "RPC method denied")
                detail = command_params(operation, body["params"])
                now = int(time.time()*1000)
                command_id = str(uuid.uuid4())
                envelope = {"command_id":command_id, "created_at_ms":now,
                            "expires_at_ms":now+900000, **detail}
                self.audit(actor,station,operation,{"command_id":command_id,**detail},"ADMITTED")
                if operation=="set_config":
                    self.service.request("POST",f"/api/plugins/telemetry/DEVICE/{station}/SHARED_SCOPE",detail)
                # Persistent native RPC is a request only, not device application.
                result = self.service.request("POST", f"/api/rpc/oneway/{station}", {
                    "method": operation, "params": envelope, "persistent": True,
                    "expirationTime": envelope["expires_at_ms"], "requestUUID":command_id,
                    "timeout":10000})
                detail = {"command_id":command_id,**detail}
                response = {"command_id":command_id,"state":"PENDING","rpc":result}
            elif action == "ack":
                require("ack" in CAPABILITIES[role], 403, "operation denied")
                require(set(body)=={"alarm_id"}, 400, "invalid ack fields")
                try:
                    ident = str(uuid.UUID(body["alarm_id"]))
                except (ValueError, TypeError, AttributeError):
                    raise Denied(400, "invalid alarm id") from None
                alarm = user_api.request("GET", "/api/alarm/"+ident)
                require(alarm["originator"]=={"entityType":"DEVICE","id":station},403,"alarm scope denied")
                detail = {"alarm_id":ident}
                self.audit(actor,station,operation,detail,"ADMITTED")
                # ACK is an idempotent business operation. The native API
                # returns 400 on a repeated ACK, including concurrent requests.
                if not alarm.get("acknowledged"):
                    try:
                        self.service.request("POST", f"/api/alarm/{ident}/ack")
                    except ApiError as e:
                        if e.status != 400 or not user_api.request("GET", "/api/alarm/"+ident).get("acknowledged"):
                            raise
                response = {"acknowledged":True}
            elif action == "export":
                require("export" in CAPABILITIES[role],403,"operation denied")
                require(set(body)=={"startTs","endTs"},400,"invalid export fields")
                start,end = body["startTs"],body["endTs"]
                require(type(start) is int and type(end) is int and 0 <= start < end
                        and end-start <= 31*86400000,400,"invalid export range")
                detail = body
                # Task 15 formats and aggregates these raw, scoped points.
                response = user_api.request("GET",
                    f"/api/plugins/telemetry/DEVICE/{station}/values/timeseries",
                    keys=HISTORY_KEYS,startTs=start,endTs=end,agg="NONE",limit=10000)
            else:
                raise Denied(404,"unknown operation")
            self.audit(actor,station,operation,detail,"ACCEPTED")
            return response
        except Denied as e:
            self.audit(actor,station,operation if isinstance(operation,str) and operation in
                       ("export","ack","test_buzzer","request_stop_irrigation","set_config","rpc")
                       else "unknown",{},"DENIED")
            raise
        except ApiError as e:
            self.audit(actor,station,action,{**detail,"upstream_status":e.status},"UPSTREAM_ERROR")
            if e.status in (401,403,404):
                raise Denied(403,"upstream permission denied") from None
            raise Denied(502,"ThingsBoard rejected request") from None

def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            # No request line, token or query string in logs.
            pass

        def reply(self, status, value):
            data=json.dumps(value,allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Length",str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            try:
                match=ROUTE.fullmatch(self.path)
                require(match is not None,404,"unknown endpoint")
                require(self.headers.get_content_type()=="application/json",415,"JSON required")
                require(not self.headers.get("Transfer-Encoding"),400,"unsupported framing")
                try:
                    length=int(self.headers.get("Content-Length","0"))
                except ValueError:
                    raise Denied(400,"invalid length") from None
                require(0 < length <= 8192,413,"invalid body size")
                self.connection.settimeout(10)
                try:
                    body=json.loads(self.rfile.read(length))
                except (ValueError, UnicodeError):
                    raise Denied(400,"invalid JSON") from None
                response=app.execute(self.headers.get("X-Authorization"),*match.groups(),body)
                self.reply(200,response)
            except Denied as e:
                self.reply(e.status,{"error":e.message})
            except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error):
                self.reply(503,{"error":"service unavailable"})
    return Handler

def make_server(app, host, port, cert, key):
    if host not in ("127.0.0.1","::1"):
        raise ValueError("bind loopback; expose only through trusted HTTPS reverse proxy")
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert,key)
    server=ThreadingHTTPServer((host,port),handler_for(app))
    server.socket=context.wrap_socket(server.socket,server_side=True)
    return server

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url",required=True)
    p.add_argument("--ca-file")
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--audit",required=True)
    p.add_argument("--cert",required=True)
    p.add_argument("--key",required=True)
    p.add_argument("--port",type=int,default=8444)
    a=p.parse_args()
    os.umask(0o077)
    app=FarmApi(a.url,a.ca_file,json.loads(a.manifest.read_text()),
                os.environ["FARM_CONTROL_API_KEY"],a.audit)
    with make_server(app,"127.0.0.1",a.port,a.cert,a.key) as server:
        server.serve_forever()

if __name__=="__main__":
    main()
