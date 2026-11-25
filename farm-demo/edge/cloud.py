"""ThingsBoard-shaped MQTT + historical REST adapter.

Production endpoints/credentials/provisioning are configured in Task 10/12.
The explicit loopback demo mode is only for the isolated test Cloud.
"""
import json
import threading
import urllib.request
import urllib.parse
import urllib.error
import ssl
import paho.mqtt.client as mqtt
from sync import CloudUnavailable

class MqttRestCloud:
    def __init__(self, host, port, base_url, devices, *, token=None, api_token=None,
                 demo=False, timeout=5, ca_file=None):
        parsed=urllib.parse.urlsplit(base_url)
        if demo:
            if host!="127.0.0.1" or parsed.hostname!="127.0.0.1" or parsed.scheme!="http":
                raise ValueError("demo transport requires loopback")
        elif parsed.scheme!="https" or not token or not api_token:
            raise ValueError("production transport requires HTTPS and both credentials")
        self.host,self.port,self.base_url=host,port,base_url.rstrip("/")
        self.devices,self.timeout=devices,timeout
        self.api_token=api_token
        self.client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
        if token:
            self.client.username_pw_set(token)
        if not demo:
            self.client.tls_set_context(ssl.create_default_context(cafile=ca_file))
        self.context=ssl.create_default_context(cafile=ca_file)
        self.ready=threading.Event()
        self.client.on_connect=lambda c,u,f,r,p: self.ready.set() if not r.is_failure else None
        self.client.on_disconnect=lambda c,u,f,r,p: self.ready.clear()
        self.running=False
        self.announced=set()

    def connect(self):
        self.close()
        try:
            self.client.connect(self.host,self.port,keepalive=30)
            self.client.loop_start()
            self.running=True
            if not self.ready.wait(self.timeout):
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

    def read(self,payload):
        if not self.ready.is_set():
            raise CloudUnavailable("MQTT disconnected")
        result={}
        for device,records in payload.items():
            if device not in self.devices:
                raise CloudUnavailable("device has no provisioned REST ID")
            keys=sorted({k for r in records for k in r["values"] if k!="sync_mode"})
            stamps={r["ts"] for r in records}
            query=urllib.parse.urlencode(dict(keys=",".join(keys),startTs=min(stamps),
                endTs=max(stamps)+1,agg="NONE",limit=10000))
            ident=urllib.parse.quote(self.devices[device],safe="")
            url=f"{self.base_url}/api/plugins/telemetry/DEVICE/{ident}/values/timeseries?{query}"
            headers={"X-Authorization":"Bearer "+self.api_token} if self.api_token else {}
            try:
                with urllib.request.urlopen(urllib.request.Request(url,headers=headers),
                        timeout=self.timeout,context=self.context) as response:
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
