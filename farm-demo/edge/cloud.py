"""ThingsBoard-shaped MQTT + historical REST adapter.

Task 10: real ThingsBoard Gateway MQTT and historical REST.
The explicit loopback demo mode is only for the isolated test Cloud.
"""
import json
import logging
import sqlite3
import threading
import urllib.request
import urllib.parse
import urllib.error
import ssl
import paho.mqtt.client as mqtt
from sync import CloudUnavailable

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class MqttRestCloud:
    def __init__(self, host, port, base_url, devices, *, token=None, api_token=None,
                 demo=False, timeout=5, ca_file=None, api_key=None, rpc_receiver=None):
        parsed=urllib.parse.urlsplit(base_url)
        if demo:
            if host!="127.0.0.1" or parsed.hostname!="127.0.0.1" or parsed.scheme!="http":
                raise ValueError("demo transport requires loopback")
        elif parsed.scheme!="https" or not token or not (api_token or api_key):
            raise ValueError("production transport requires HTTPS and both credentials")
        self.host,self.port,self.base_url=host,port,base_url.rstrip("/")
        self.devices,self.timeout=devices,timeout
        if api_token and api_key:
            raise ValueError("choose one REST credential")
        self.headers = ({"X-Authorization": "ApiKey " + api_key.removeprefix("ApiKey ")}
                        if api_key else {"X-Authorization": "Bearer " + api_token} if api_token else {})
        self.client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311, manual_ack=rpc_receiver is not None)
        if token:
            self.client.username_pw_set(token)
        if not demo:
            self.client.tls_set_context(ssl.create_default_context(cafile=ca_file))
        self.context=ssl.create_default_context(cafile=ca_file)
        self.opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=self.context),NoRedirect())
        self.ready=threading.Event()
        self.rpc_receiver=rpc_receiver
        self.client.on_connect=self.on_connect
        self.client.on_subscribe=self.on_subscribe
        self.client.on_message=self.on_message
        self.client.on_disconnect=self.on_disconnect
        self.running=False
        self.announced=set()

    def on_connect(self, client, userdata, flags, reason, properties):
        if not reason.is_failure:
            if self.rpc_receiver is None:
                self.ready.set()
            else:
                client.subscribe("v1/gateway/rpc",qos=1)

    def on_subscribe(self, client, userdata, mid, reasons, properties):
        if not any(r.is_failure for r in reasons): self.ready.set()

    def on_message(self, client, userdata, message):
        try:
            if self.rpc_receiver and message.topic=="v1/gateway/rpc" and not message.retain:
                self.rpc_receiver(message.payload)
            if message.qos and self.rpc_receiver:
                if client.ack(message.mid,message.qos)!=mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError("RPC MQTT ACK failed")
        except (sqlite3.Error, OSError, RuntimeError):
            logging.getLogger("farm.rpc").error("RPC inbox unavailable")
            client.disconnect()

    def on_disconnect(self, client, userdata, flags, reason, properties):
        self.ready.clear()
        self.announced.clear()

    def connect(self):
        self.close()
        try:
            self.client.connect(self.host,self.port,keepalive=30)
            self.client.loop_start()
            self.running=True
            if not self.ready.wait(self.timeout):
                self.close()
                raise CloudUnavailable("MQTT connection not confirmed")
        except (OSError, RuntimeError) as exc:
            self.close()
            raise CloudUnavailable("MQTT connection failed") from exc

    def close(self):
        if self.running:
            self.client.disconnect()
            self.client.loop_stop()
            self.running=False
        self.ready.clear()
        self.announced.clear()

    def send(self,topic,payload):
        if not self.ready.is_set():
            raise CloudUnavailable("MQTT disconnected")
        try:
            info=self.client.publish(topic,json.dumps(payload,allow_nan=False),qos=1)
            info.wait_for_publish(timeout=self.timeout)
            if not info.is_published():
                raise CloudUnavailable("MQTT PUBACK timeout")
        except (ValueError,RuntimeError,OSError) as exc:
            raise CloudUnavailable("MQTT publish failed") from exc

    def publish(self,payload):
        for device in payload:
            if device not in self.announced:
                self.send("v1/gateway/connect",{"device":device})
                self.announced.add(device)
        self.send("v1/gateway/telemetry",payload)

    def publish_health(self, health, now):
        self.send("v1/devices/me/telemetry", {"ts": now, "values": {
            "farm_health": True, "disk_bytes": health["disk_bytes"],
            "budget_bytes": health["budget_bytes"], "pending": health["pending"],
            "oldest_age_ms": health["oldest_age_ms"] or 0, "link_state": health["state"],
            "radio_diagnostics": json.dumps(health.get("radio_diagnostics",{}),sort_keys=True,allow_nan=False)}})

    def read(self,payload):
        if not self.ready.is_set():
            raise CloudUnavailable("MQTT disconnected")
        result={}
        for device,records in payload.items():
            if device not in self.devices:
                raise CloudUnavailable("device has no provisioned REST ID")
            keys=sorted({k for r in records for k in r["values"] if k not in ("sync_mode","sync_state")})
            stamps={r["ts"] for r in records}
            query=urllib.parse.urlencode(dict(keys=",".join(keys),startTs=max(0,min(stamps)-1),
                endTs=max(stamps)+1,agg="NONE",limit=10000))
            ident=urllib.parse.quote(self.devices[device],safe="")
            url=f"{self.base_url}/api/plugins/telemetry/DEVICE/{ident}/values/timeseries?{query}"
            headers=self.headers
            try:
                with self.opener.open(urllib.request.Request(url,headers=headers),timeout=self.timeout) as response:
                    data=json.load(response)
                for key,points in data.items():
                    if key not in keys:
                        continue
                    for point in points:
                        ts=int(point["ts"])
                        if ts in stamps:
                            bucket=result.setdefault((device,ts),{})
                            if key in bucket and bucket[key]!=point["value"]:
                                raise ValueError("ambiguous history response")
                            bucket[key]=point["value"]
            except (OSError,ValueError,TypeError,KeyError,AttributeError) as exc:
                raise CloudUnavailable("history readback failed") from exc
        return result
