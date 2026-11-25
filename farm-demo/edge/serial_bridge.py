"""Convert delivered simulator RADIO_TX bytes into MQTT events, ACK at logical RX.

Transport adapter only: a caller provides the simulator's line stream and writes
returned commands back to the UART. The broker is real; RF is not simulated here.
"""
import base64
import datetime as dt
import json
import re
from weather_pb2 import WeatherAck

TX = re.compile(r"RADIO_TX seq=(\d+) fcnt=(\d+) fport=(\d+) hex=([0-9a-f]+) delivered=([01]) ts=(\d+)")

class SerialBridge:
    def __init__(self, app, dev_eui, publish, *, epoch_s=1795453200):
        self.app, self.dev, self.publish, self.epoch_s = app, dev_eui, publish, epoch_s
        self.active = None
        self.ack = None
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
            if command["devEui"] != self.dev or command["fPort"] != 20:
                return
            data = base64.b64decode(command["data"], validate=True)
            ack = WeatherAck.FromString(data)
            if list(ack.sequence_number) == [self.active]:
                self.ack = data.hex()
        except (ValueError, KeyError, TypeError):
            return

    def on_line(self, line):
        line = line.strip()
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
        if line in ("RX1", "RX2") and self.ack:
            command = "DOWNLINK 20 "+self.ack
            self.active, self.ack = None, None
            return command
        if line == "RX2":
            self.active = None
        return None
