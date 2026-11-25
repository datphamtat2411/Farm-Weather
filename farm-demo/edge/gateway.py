"""ChirpStack v4-shaped MQTT uplink -> validated SQLite commit -> explicit ACK."""
import argparse
import base64
import binascii
import datetime as dt
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

from outbox import connect, store, quarantine, CapacityError, diagnostics, BUDGET
from protocol import decode, ACK_PORT
from weather_pb2 import WeatherAck
import rpc

LOG = logging.getLogger("farm.edge")
UPLINK = re.compile(r"application/([A-Za-z0-9_-]+)/device/([0-9a-f]{16})/event/up")
MIN_TS = 1577836800000  # 2020 UTC; reject uninitialized clocks, allow old backfill.
MAX_EVENT_BYTES = 16384

def require(condition, reason):
    if not condition:
        raise ValueError(reason)

def utc_ms(value):
    require(isinstance(value, str), "missing reception time")
    value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(value.tzinfo is not None, "reception time lacks timezone")
    return int(value.timestamp()*1000)

def load_mapping(path):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    require(isinstance(obj, dict) and bool(obj), "empty device mapping")
    for key, name in obj.items():
        require(UPLINK.fullmatch(f"application/{key}/event/up") is not None and
                isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name),
                "invalid mapping")
    require(len(set(obj.values())) == len(obj), "duplicate station identity")
    return obj

def parse_uplink(topic, raw, mapping):
    match = UPLINK.fullmatch(topic)
    require(match is not None, "invalid uplink topic")
    app, dev = match.groups()
    station = mapping.get(f"{app}/device/{dev}")
    require(station is not None, "unmapped device")
    require(len(raw) <= MAX_EVENT_BYTES, "oversize event")
    body = json.loads(raw)
    require(isinstance(body, dict), "invalid uplink object")
    info = body["deviceInfo"]
    require(info["applicationId"] == app and info["devEui"].lower() == dev, "device/topic mismatch")
    port = body["fPort"]
    payload = base64.b64decode(body["data"], validate=True)
    record = (dict(rpc.decode_result(payload), device_id=station) if port==22 else decode(port, payload, station))
    if port==22: record.update(timestamp=record['ts_s']*1000,sequence_number=record['sequence'])
    received = utc_ms(body["time"])
    require(received >= MIN_TS and MIN_TS <= record["timestamp"] <= received+300000,
            "untrusted measurement clock")
    require(body.get("timeTrusted", True) is True, "untrusted measurement clock")
    counter = body["fCnt"]
    require(type(counter) is int and 0 <= counter <= 0xffffffff, "invalid frame counter")
    rx = body.get("rxInfo", [])
    require(isinstance(rx, list) and len(rx) <= 64, "invalid rxInfo")
    receivers = []
    for item in rx:
        gateway_id, rssi, snr = item["gatewayId"], item["rssi"], item["snr"]
        require(isinstance(gateway_id, str) and re.fullmatch(r"[0-9a-fA-F]{16}", gateway_id)
                and type(rssi) is int and -250 <= rssi <= 0 and type(snr) in (int,float)
                and -100 <= snr <= 100, "invalid receiver metadata")
        receivers.append(dict(gateway_id=gateway_id.lower(), rssi=rssi, snr=snr))
    return record, dict(received_at=received, dev_eui=dev, application_id=app,
                        fcnt=counter, fport=port, receivers=receivers,
                        simulated=body.get("simulation") is True), app, dev

def handle(db, mapping, topic, raw, *, budget=BUDGET):
    """No ACK for invalid, conflicting or uncommitted data. Return status and downlink."""
    try:
        record, meta, app, dev = parse_uplink(topic, raw, mapping)
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, binascii.Error) as exc:
        # Store a bounded reason and raw excerpt; never print payloads/secrets.
        quarantine(db, f"invalid uplink: {type(exc).__name__}: {str(exc)[:160]}", topic, raw)
        return "invalid", None
    rpc.schema(db)
    if meta['fport']==22:
        try:
            result={k:v for k,v in record.items() if k not in ('device_id','timestamp','sequence_number')}
            status=rpc.record_result(db,record['device_id'],result,meta['received_at'] if not meta['simulated'] else rpc.now_ms())
        except ValueError:
            quarantine(db,'invalid/conflicting command result',topic,raw)
            return 'invalid',None
    else:
        if db.execute('SELECT 1 FROM command_receipts WHERE station=? AND sequence=?',(record['device_id'],record['sequence_number'])).fetchone():
            quarantine(db,'weather/command sequence collision',topic,raw)
            return 'conflict',None
        status = store(db, record, meta, budget=budget)
    if status == "conflict":
        quarantine(db, "conflicting record identity", topic, raw, meta["received_at"])
        return status, None
    ack = WeatherAck(sequence_number=[record["sequence_number"]]).SerializeToString(deterministic=True)
    command = dict(devEui=dev, confirmed=False, fPort=ACK_PORT,
                   data=base64.b64encode(ack).decode("ascii"))
    return status, (f"application/{app}/device/{dev}/command/down", command)

def make_client(client_id):
    import paho.mqtt.client as mqtt
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                       clean_session=False, protocol=mqtt.MQTTv311, manual_ack=True)

def run_service(args):
    mapping = load_mapping(args.mapping)
    client = make_client(args.client_id)
    if os.environ.get("EDGE_MQTT_USERNAME"):
        client.username_pw_set(os.environ["EDGE_MQTT_USERNAME"], os.environ.get("EDGE_MQTT_PASSWORD"))
    fatal = []
    # All callbacks use one database connection on the network-loop thread.
    db = connect(args.db, recover=False)
    rpc.schema(db)
    def on_connect(c, userdata, flags, reason, properties):
        if reason.is_failure:
            fatal.append(str(reason))
            c.disconnect()
            return
        for app in sorted({key.split("/")[0] for key in mapping}):
            c.subscribe(f"application/{app}/device/+/event/up", qos=1)
    def on_message(c, userdata, message):
        try:
            if message.retain:
                quarantine(db, "retained uplink rejected", message.topic, message.payload)
                outcome, down = "invalid", None
            else:
                outcome, down = handle(db, mapping, message.topic, message.payload)
            if down:
                topic, command = down
                publication = c.publish(topic, json.dumps(command), qos=1, retain=False)
                if publication.rc != 0:
                    raise RuntimeError("ACK publish not queued")
                record, meta, app, dev = parse_uplink(message.topic,message.payload,mapping)
                pending=rpc.next_downlink(db,record["device_id"],app,dev,
                    logical_ts=meta["received_at"] if meta["simulated"] else None)
                if pending and c.publish(pending[0],json.dumps(pending[1]),qos=1,retain=False).rc != 0:
                    raise RuntimeError("RPC downlink not queued")
            if message.qos:
                if c.ack(message.mid, message.qos) != 0:
                    raise RuntimeError("MQTT acknowledgement failed")
            LOG.info("uplink=%s health=%s", outcome, diagnostics(db))
        except (sqlite3.Error, CapacityError, RuntimeError) as exc:
            # Leave MQTT delivery unacknowledged. A supervisor can restart the
            # persistent session; the station will also retry the same record.
            LOG.error("Edge unavailable: %s", type(exc).__name__)
            fatal.append(type(exc).__name__)
            c.disconnect()
    def on_subscribe(c, userdata, mid, reasons, properties):
        if any(reason.is_failure for reason in reasons):
            fatal.append("subscription rejected")
            c.disconnect()
            return
        LOG.info("EDGE_SUBSCRIBED")
    client.on_connect, client.on_message, client.on_subscribe = on_connect, on_message, on_subscribe
    try:
        client.connect(args.host, args.port, keepalive=30)
        client.loop_forever()
        if fatal:
            raise SystemExit(1)
    finally:
        db.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--client-id", default="farm-edge-v1")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run_service(parser.parse_args())

if __name__ == "__main__":
    main()
