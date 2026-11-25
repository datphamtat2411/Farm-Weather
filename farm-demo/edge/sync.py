"""Task 09: durable, single-writer Cloud delivery with independent readback.

Transport contract: connect(), publish(payload), read(payload), close().
read returns { (device, ts): {key: value} } from persisted history, not PUBACK.
The worker owns its DB connection; gateway ingestion runs independently.
"""
import json
import logging
import random
import time
from decimal import Decimal, InvalidOperation
from outbox import now_ms, diagnostics

LOG = logging.getLogger("farm.sync")
MAX_BATCH = 50
RETENTION_MS = 30 * 86400000
BACKOFF = (5, 10, 20, 40, 60)

class CloudUnavailable(RuntimeError):
    """Transient connection, publish or readback failure."""

def delay_ms(attempt, jitter=random.uniform):
    base = BACKOFF[min(max(1, attempt)-1, len(BACKOFF)-1)]
    # Non-negative jitter, capped at 60 s; no busy retry.
    return int(min(60, base + jitter(0, base*.1))*1000)

def values(record):
    identity = f'{record["device_id"]}:{record["sequence_number"]}'
    event = record.get("kind") == "event"
    data = {k:v for k,v in record.items()
            if k not in ("device_id", "timestamp", "kind")}
    if event:
        # Every event key is separate, including status and ambient measurements.
        data = {"event_"+k:v for k,v in data.items()}
        data["event_record_id"] = identity
    else:
        data["record_id"] = identity
    return data

def same(expected, actual):
    # ThingsBoard REST represents scalar values as strings.
    if type(expected) is bool:
        return actual is expected or actual == str(expected).lower()
    if type(expected) in (int, float):
        if type(actual) is bool or actual is None:
            return False
        try:
            return Decimal(str(expected)) == Decimal(str(actual))
        except (InvalidOperation, ValueError):
            return False
    return type(actual) is type(expected) and actual == expected

def compare(expected, actual):
    """Verified, missing/partial, or conflicting persisted content."""
    identity = "event_record_id" if "event_record_id" in expected else "record_id"
    if identity in actual and not same(expected[identity], actual[identity]):
        return "conflict"
    if any(k in actual and not same(v, actual[k]) for k,v in expected.items()):
        return "conflict"
    return "verified" if all(k in actual for k in expected) else "missing"

class Synchronizer:
    def __init__(self, db, cloud, *, clock=now_ms, jitter=random.uniform):
        self.db, self.cloud, self.clock, self.jitter = db, cloud, clock, jitter
        self.state = "OFFLINE"
        self.connected = False
        self.failures = 0
        self.reconnect_at = 0
        self.next_batch_at = 0

    def status(self):
        return dict(state=self.state, **diagnostics(self.db, clock_ms=self.clock()))

    def offline(self, now):
        self.connected = False
        self.state = "OFFLINE"
        self.failures += 1
        self.reconnect_at = now + delay_ms(self.failures, self.jitter)
        self.cloud.close()

    def claim(self, now):
        # Give one fresh record a fast lane, then FIFO per device for the
        # remainder. At most one fast-lane slot per batch prevents starvation.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            candidates = self.db.execute("""SELECT device_id,sequence_number,ts_ms,kind,
                payload_json,attempts FROM weather_outbox
                WHERE state='PENDING' AND next_retry_at<=?
                ORDER BY ts_ms,device_id,sequence_number LIMIT ?""",(now, MAX_BATCH)).fetchall()
            fast = self.db.execute("""SELECT device_id,sequence_number,ts_ms,kind,payload_json,attempts
                FROM weather_outbox WHERE state='PENDING' AND next_retry_at<=?
                AND ts_ms BETWEEN ? AND ? AND kind='periodic'
                ORDER BY ts_ms DESC,device_id,sequence_number DESC LIMIT 1""",
                (now,now-1800000,now)).fetchone()
            if fast and fast not in candidates:
                candidates = [fast] + candidates[:MAX_BATCH-1]
            elif fast:
                candidates = [fast] + [r for r in candidates if r != fast]
            self.db.executemany("""UPDATE weather_outbox SET state='IN_FLIGHT',attempts=attempts+1
                WHERE device_id=? AND sequence_number=?""",[(r[0],r[1]) for r in candidates])
            self.db.commit()
            return candidates
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, row, result, now):
        dev, seq, ts, kind, payload, attempts = row
        if result == "verified":
            self.db.execute("""UPDATE weather_outbox SET state='SYNCED',synced_at=?,next_retry_at=0
                WHERE device_id=? AND sequence_number=? AND state='IN_FLIGHT'""",(now,dev,seq))
        elif result == "conflict":
            self.db.execute("""UPDATE weather_outbox SET state='QUARANTINE'
                WHERE device_id=? AND sequence_number=? AND state='IN_FLIGHT'""",(dev,seq))
            LOG.error("Cloud identity/content conflict device=%s seq=%s",dev,seq)
        else:
            self.db.execute("""UPDATE weather_outbox SET state='PENDING',next_retry_at=?
                WHERE device_id=? AND sequence_number=? AND state='IN_FLIGHT'""",
                (now+delay_ms(attempts+1,self.jitter),dev,seq))

    def make_payload(self, rows, now):
        packet = {}
        for row in rows:
            dev,seq,ts,kind,raw,_ = row
            rec = json.loads(raw)
            data = values(rec)
            period = rec.get("period_s",rec.get("requested_period_s",300))
            latest = self.db.execute("""SELECT max(ts_ms) FROM weather_outbox
                WHERE device_id=? AND kind=? AND state!='QUARANTINE'""",(dev,kind)).fetchone()[0]
            # Historical events must not transiently raise an alarm when a
            # newer station snapshot is already buffered at the Edge.
            newest_state = self.db.execute("""SELECT sequence_number FROM weather_outbox
                WHERE device_id=? AND state!='QUARANTINE'
                ORDER BY ts_ms DESC, sequence_number DESC LIMIT 1""",(dev,)).fetchone()
            data["sync_mode"] = "live" if ts == latest and 0 <= now-ts <= 2*period*1000 else "history"
            if kind == "event" and newest_state and seq != newest_state[0]:
                data["sync_mode"] = "history"
            # A still-fresh periodic sample may refresh weather values but not
            # the confirmed alarm state when a newer event is already known.
            if kind == "periodic" and newest_state and seq != newest_state[0] and data["sync_mode"] == "live":
                data["sync_state"] = "history"
            packet.setdefault(dev,[]).append(dict(ts=ts,values=data))
        for records in packet.values():
            records.sort(key=lambda r:r["ts"])
        return packet

    def maintain(self, now):
        # Keep hashes/identity for at least 30 days. Reclaim bulky contents
        # after one day, then expire only already verified keys.
        with self.db:
            self.db.execute("""UPDATE weather_outbox SET payload_json='',metadata_json='{}'
                WHERE state='SYNCED' AND synced_at<=? AND payload_json!=''""",(now-86400000,))
            self.db.execute("DELETE FROM weather_outbox WHERE state='SYNCED' AND synced_at<=?",
                            (now-RETENTION_MS,))

    def step(self):
        now = self.clock()
        if now < self.next_batch_at:
            return 0
        if not self.connected:
            if now < self.reconnect_at:
                return 0
            try:
                self.cloud.connect()
            except CloudUnavailable:
                self.offline(now)
                return 0
            self.connected = True
            self.state = "RECOVERING"
        rows = self.claim(now)
        if not rows:
            # Probe even with an empty queue so disconnect is not masked.
            try:
                self.cloud.read({})
            except CloudUnavailable:
                self.offline(now)
                return 0
            self.failures = 0
            self.state = "RECOVERING" if self.status()["pending"] else "ONLINE"
            self.next_batch_at = now+1000
            self.maintain(now)
            return 0
        self.next_batch_at = now+1000
        self.state = "RECOVERING"
        unfinished = list(rows)
        try:
            # Same kind + same timestamp cannot coexist in ThingsBoard's
            # key/timestamp model. Keep earliest sequence; quarantine collision.
            accepted=[]
            with self.db:
                for row in rows:
                    dev,seq,ts,kind,_,_=row
                    earlier=self.db.execute("""SELECT 1 FROM weather_outbox WHERE device_id=?
                        AND ts_ms=? AND kind=? AND sequence_number<? AND state!='QUARANTINE' LIMIT 1""",
                        (dev,ts,kind,seq)).fetchone()
                    if earlier:
                        self.finish(row,"conflict",now)
                        unfinished.remove(row)
                    else:
                        accepted.append(row)
            packet=self.make_payload(accepted,now)
            before=self.cloud.read(packet)
            to_send=[]
            with self.db:
                for row in accepted:
                    dev,seq,ts,kind,raw,_=row
                    verdict=compare(values(json.loads(raw)),before.get((dev,ts),{}))
                    if verdict=="missing":
                        to_send.append(row)
                    else:
                        self.finish(row,verdict,now)
                        unfinished.remove(row)
            if to_send:
                packet=self.make_payload(to_send,now)
                self.cloud.publish(packet)
                after=self.cloud.read(packet)
                with self.db:
                    for row in to_send:
                        dev,seq,ts,kind,raw,_=row
                        self.finish(row,compare(values(json.loads(raw)),after.get((dev,ts),{})),self.clock())
                        unfinished.remove(row)
            self.failures=0
            self.state="RECOVERING" if self.status()["pending"] else "ONLINE"
        except CloudUnavailable:
            with self.db:
                for row in unfinished:
                    self.finish(row,"missing",self.clock())
            self.offline(self.clock())
        self.maintain(self.clock())
        return len(rows)
