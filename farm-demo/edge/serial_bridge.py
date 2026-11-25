"""Convert delivered simulator RADIO_TX bytes into MQTT events, ACK at logical RX.

Transport adapter only: a caller provides the simulator's line stream and writes
returned commands back to the UART. The broker is real; RF is not simulated here.
"""
import base64
import datetime as dt
import json
import re
from weather_pb2 import WeatherAck
from google.protobuf.message import DecodeError

TX = re.compile(r"RADIO_TX seq=(\d+) fcnt=(\d+) fport=(\d+) hex=([0-9a-f]+) delivered=([01]) ts=(\d+)")

class SerialBridge:
    def __init__(self, app, dev_eui, publish, *, epoch_s=1795453200):
        self.app, self.dev, self.publish, self.epoch_s = app, dev_eui, publish, epoch_s
        self.active = None
        self.ack = None
        self.command = None
        self.offset = 0
        self.trusted = False
        self.manual = False

    @property
    def up_topic(self):
        return f"application/{self.app}/device/{self.dev}/event/up"

    @property
    def down_topic(self):
        return f"application/{self.app}/device/{self.dev}/command/down"

    def on_downlink(self, topic, raw):
        if topic != self.down_topic or self.active is None:
            return
        try:
            command = json.loads(raw)
            if command["devEui"] != self.dev:
                return
            data = base64.b64decode(command["data"], validate=True)
            if command["fPort"]==21:
                if 0<len(data)<=51: self.command=data.hex()
                return
            if command["fPort"]!=20: return
            ack = WeatherAck.FromString(data)
            if list(ack.sequence_number) == [self.active]:
                self.ack = data.hex()
        except (ValueError, KeyError, TypeError, DecodeError):
            return

    def on_line(self, line):
        line = line.strip()
        if line.startswith(("BOOT ","RADIO_TX ")) or line=="LINK_DOWN": self.command=None
        if line.startswith("BOOT "):
            self.manual, self.trusted, self.active, self.ack = False, False, None, None
            return "ACK MANUAL"
        if line == "ACK_MANUAL":
            self.manual = True
        match = re.fullmatch(r"SIM_RTC offset=(\d+) trusted=([01])", line)
        if match:
            self.offset, self.trusted = int(match[1]), match[2] == "1"
        if line == "LINK_DOWN":
            self.active, self.ack = None, None
        tx = TX.fullmatch(line)
        if tx:
            if not self.manual:
                raise RuntimeError("simulator must confirm ACK MANUAL before bridge ingress")
            seq, fcnt, port, wire, delivered, logical = tx.groups()
            self.active, self.ack = (int(seq) if delivered == "1" else None), None
            if self.active is not None:
                received = dt.datetime.fromtimestamp(self.epoch_s+self.offset+int(logical), dt.timezone.utc)
                event = dict(deviceInfo=dict(applicationId=self.app,devEui=self.dev),
                             fPort=int(port), fCnt=int(fcnt), data=base64.b64encode(bytes.fromhex(wire)).decode(),
                             time=received.isoformat(), timeTrusted=self.trusted, simulation=True, rxInfo=[])
                self.publish(self.up_topic, json.dumps(event))
        if line=="RX1" and self.active is not None and self.command:
            command="DOWNLINK 21 "+self.command
            self.command=None
            return command
        if line in ("RX1", "RX2") and self.ack:
            command = "DOWNLINK 20 "+self.ack
            self.active, self.ack = None, None
            return command
        if line == "RX2":
            self.active = None
        return None

def main():
    """Attach to a bidirectional raw UART TCP endpoint provided by a simulator host."""
    import argparse
    import os
    import socket
    import threading
    import paho.mqtt.client as mqtt
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--serial-host", default="127.0.0.1")
    parser.add_argument("--serial-port", type=int, required=True)
    parser.add_argument("--broker-host", default="127.0.0.1")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--app", required=True)
    parser.add_argument("--dev-eui", required=True)
    args = parser.parse_args()
    lock = threading.Lock()
    ready = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if os.environ.get("SIM_MQTT_USERNAME"):
        client.username_pw_set(os.environ["SIM_MQTT_USERNAME"], os.environ.get("SIM_MQTT_PASSWORD"))
    def publish(topic, body):
        if client.publish(topic, body, qos=1).rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError("simulator uplink publish failed")
    bridge = SerialBridge(args.app,args.dev_eui,publish)
    def connected(c, userdata, flags, reason, props):
        if not reason.is_failure:
            c.subscribe(bridge.down_topic,qos=1)
    def subscribed(c, userdata, mid, reasons, props):
        if not any(reason.is_failure for reason in reasons):
            ready.set()
    def down(c, userdata, message):
        if not message.retain:
            with lock:
                bridge.on_downlink(message.topic,message.payload)
    client.on_connect,client.on_subscribe,client.on_message=connected,subscribed,down
    client.connect(args.broker_host,args.broker_port)
    client.loop_start()
    try:
        if not ready.wait(10):
            raise RuntimeError("MQTT subscription unavailable")
        with socket.create_connection((args.serial_host,args.serial_port),timeout=10) as uart:
            uart.settimeout(None)
            with uart.makefile("r",encoding="utf-8",errors="strict") as lines:
                uart.sendall(b"ACK MANUAL\n")
                for line in lines:
                    with lock:
                        command=bridge.on_line(line)
                    if command:
                        uart.sendall((command+"\n").encode())
    finally:
        client.disconnect()
        client.loop_stop()

if __name__ == "__main__":
    main()
