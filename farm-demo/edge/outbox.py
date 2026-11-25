"""Durable Edge inbox/outbox; no Cloud confirmation is inferred here."""
import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

BUDGET = 64 * 1024 * 1024

class CapacityError(RuntimeError):
    pass

def now_ms():
    return time.time_ns() // 1_000_000

def canonical(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)

def connect(path, *, recover=True):
    db = sqlite3.connect(path, timeout=5)
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA wal_autocheckpoint=256")
    with db:
        db.execute("""CREATE TABLE IF NOT EXISTS weather_outbox (
            device_id TEXT NOT NULL, sequence_number INTEGER NOT NULL,
            ts_ms INTEGER NOT NULL, payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'PENDING',
            kind TEXT NOT NULL DEFAULT 'periodic', received_at INTEGER NOT NULL DEFAULT 0,
            priority INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
            next_retry_at INTEGER NOT NULL DEFAULT 0, synced_at INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(device_id,sequence_number))""")
        # Preserve old smoke databases without treating their diagnostic JSON as
        # validated Protobuf records. Never silently acknowledge a legacy row.
        columns = {row[1] for row in db.execute("PRAGMA table_info(weather_outbox)")}
        if "kind" not in columns:
            for name, definition in (
                ("kind", "TEXT NOT NULL DEFAULT 'periodic'"),
                ("received_at", "INTEGER NOT NULL DEFAULT 0"),
                ("priority", "INTEGER NOT NULL DEFAULT 0"),
                ("attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("next_retry_at", "INTEGER NOT NULL DEFAULT 0"),
                ("synced_at", "INTEGER"),
                ("metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                db.execute(f"ALTER TABLE weather_outbox ADD COLUMN {name} {definition}")
            db.execute("UPDATE weather_outbox SET state='QUARANTINE'")
        db.execute("""CREATE INDEX IF NOT EXISTS outbox_due
            ON weather_outbox(state, priority DESC, next_retry_at, ts_ms)""")
        db.execute("""CREATE TABLE IF NOT EXISTS quarantine (
            id INTEGER PRIMARY KEY, received_at INTEGER NOT NULL,
            reason TEXT NOT NULL, topic TEXT NOT NULL, raw_excerpt TEXT NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE, occurrences INTEGER NOT NULL DEFAULT 1)""")
        db.execute("""CREATE TABLE IF NOT EXISTS edge_health (
            id INTEGER PRIMARY KEY CHECK(id=1), near_full INTEGER NOT NULL DEFAULT 0)""")
        db.execute("INSERT OR IGNORE INTO edge_health(id) VALUES(1)")
        if recover:
            db.execute("UPDATE weather_outbox SET state='PENDING' WHERE state='IN_FLIGHT'")
    return db

def disk_bytes(db):
    # Include index pages and WAL; never use a fixed bytes-per-record estimate.
    pages = db.execute("PRAGMA page_count").fetchone()[0]
    size = db.execute("PRAGMA page_size").fetchone()[0] * pages
    filename = db.execute("PRAGMA database_list").fetchone()[2]
    if filename:
        wal = Path(filename + "-wal")
        if wal.exists():
            size += wal.stat().st_size
    return size

def quarantine(db, reason, topic, raw, received_at=None):
    raw = bytes(raw)
    fingerprint = hashlib.sha256(topic.encode() + b"\0" + reason.encode() + b"\0" + raw).hexdigest()
    with db:
        # Bounded diagnostic evidence, independent of pending weather rows.
        db.execute("""INSERT INTO quarantine(received_at,reason,topic,raw_excerpt,fingerprint)
            VALUES(?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET occurrences=occurrences+1""",
            (received_at if received_at is not None else now_ms(), reason[:256], topic[:512],
             raw[:2048].decode("utf-8", errors="replace"), fingerprint))
        db.execute("DELETE FROM quarantine WHERE id NOT IN (SELECT id FROM quarantine ORDER BY id DESC LIMIT 256)")

def store(db, record, metadata, *, budget=BUDGET):
    """Return inserted/duplicate/conflict. Commit before returning an ACK-eligible result."""
    device, seq, ts = record["device_id"], record["sequence_number"], record["timestamp"]
    kind = record.get("kind", "periodic")
    if (not isinstance(device, str) or not device or type(seq) is not int or
            not 0 < seq <= 0xffffffff or type(ts) is not int or ts <= 0 or
            kind not in ("periodic", "event")):
        raise ValueError("invalid record identity")
    payload = canonical(record)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    key = (device, seq)
    # Explicit writer transaction serializes capacity, identity and insert checks.
    db.execute("BEGIN IMMEDIATE")
    try:
        existing = db.execute("SELECT payload_hash,state FROM weather_outbox WHERE device_id=? AND sequence_number=?", key).fetchone()
        if existing:
            result = "duplicate" if existing[0] == digest and existing[1] != "QUARANTINE" else "conflict"
        else:
            # Soft 64 MiB budget including WAL/indexes, with headroom for one
            # transaction. Refuse periodic records earlier to reserve event space.
            limit = budget if kind == "event" else budget * 95 // 100
            if disk_bytes(db) + 65536 > limit:
                raise CapacityError("outbox capacity reserved or exhausted")
            db.execute("""INSERT INTO weather_outbox
                (device_id,sequence_number,ts_ms,payload_json,payload_hash,kind,received_at,priority,metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (*key, ts, payload, digest, kind, metadata["received_at"],
                 int(kind == "event"), canonical(metadata)))
            result = "inserted"
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return result

def radio_diagnostics(db):
    result={}
    for device,raw,received in db.execute("""SELECT device_id,metadata_json,received_at FROM (
        SELECT device_id,metadata_json,received_at,ROW_NUMBER() OVER (
          PARTITION BY device_id ORDER BY received_at DESC,sequence_number DESC) AS n
        FROM weather_outbox WHERE state!='QUARANTINE') WHERE n=1"""):
        if device in result:
            continue
        metadata=json.loads(raw)
        receivers=metadata.get("receivers") or []
        best=max(receivers,key=lambda item:item["rssi"]) if receivers else {}
        result[device]={"rssi":best.get("rssi"),"snr":best.get("snr"),
                        "received_at":received,"simulated":metadata.get("simulated") is True}
    return result

def diagnostics(db, *, clock_ms=None, budget=BUDGET):
    clock_ms = now_ms() if clock_ms is None else clock_ms
    used = disk_bytes(db)
    pending, oldest = db.execute("""SELECT count(*),min(received_at)
        FROM weather_outbox WHERE state IN ('PENDING','IN_FLIGHT')""").fetchone()
    with db:
        warning = bool(db.execute("SELECT near_full FROM edge_health WHERE id=1").fetchone()[0])
        warning = used * 100 >= budget * (70 if warning else 80)
        db.execute("UPDATE edge_health SET near_full=? WHERE id=1", (int(warning),))
    return dict(pending=pending, oldest_age_ms=max(0, clock_ms-oldest) if oldest is not None else None,
                disk_bytes=used, budget_bytes=budget, near_full=warning,
                quarantined=db.execute("SELECT count(*) FROM quarantine").fetchone()[0])

# Compatibility only for the pre-Task-08 hardware smoke verifier. The live Edge
# service never ingests diagnostic JSON and never issues ACK from this path.
def ingest(db, record):
    if type(record.get("ts_s")) is not int or record["ts_s"] <= 0:
        raise ValueError("invalid timestamp")
    normalized = dict(record, timestamp=record["ts_s"]*1000)
    normalized.pop("ts_s")
    if store(db, normalized, {"received_at": now_ms(), "source": "legacy-smoke"}) == "conflict":
        raise ValueError("conflicting record")

def ingest_log(db, path):
    import re
    records = re.findall(r"TELEMETRY (\{[^{}\r\n]*\})", Path(path).read_text(encoding="utf-8"))
    if not records:
        raise ValueError("no telemetry in simulator log")
    for raw in records:
        ingest(db, json.loads(raw))
    return len(records)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Legacy diagnostic smoke importer, not gateway ingestion")
    parser.add_argument("log")
    parser.add_argument("--db", default="weather.sqlite")
    args = parser.parse_args()
    db = connect(args.db)
    try:
        print(ingest_log(db, args.log))
    finally:
        db.close()
