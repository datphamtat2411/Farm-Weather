"""Demo ingestion from Wokwi serial log. No claim of RF or Cloud delivery."""
import argparse
import hashlib
import json
import sqlite3

def connect(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("""CREATE TABLE IF NOT EXISTS weather_outbox (
        device_id TEXT NOT NULL, sequence_number INTEGER NOT NULL,
        ts_ms INTEGER NOT NULL, payload_json TEXT NOT NULL,
        payload_hash TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING',
        PRIMARY KEY(device_id,sequence_number))""")
    return db

def ingest(db, record):
    if not isinstance(record["device_id"], str) or not record["device_id"]:
        raise ValueError("invalid device_id")
    if type(record["sequence_number"]) is not int or record["sequence_number"] < 1:
        raise ValueError("invalid sequence_number")
    if type(record["ts_s"]) is not int or record["ts_s"] <= 0:
        raise ValueError("invalid timestamp")
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    key = (record["device_id"], record["sequence_number"])
    with db:
        db.execute("INSERT OR IGNORE INTO weather_outbox(device_id,sequence_number,ts_ms,payload_json,payload_hash) VALUES (?,?,?,?,?)",
                   (*key, record["ts_s"]*1000, payload, digest))
        existing = db.execute("SELECT payload_hash FROM weather_outbox WHERE device_id=? AND sequence_number=?", key).fetchone()
        if existing[0] != digest:
            raise ValueError("conflicting record")

def ingest_log(db, path):
    count = 0
    with open(path, encoding="utf-8") as log:
        for line in log:
            if "TELEMETRY {" in line:
                record = json.loads(line.split("TELEMETRY ", 1)[1])
                ingest(db, record)
                count += 1
    if not count:
        raise ValueError("no telemetry in simulator log")
    return count

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("log")
    parser.add_argument("--db", default="weather.sqlite")
    args=parser.parse_args()
    with connect(args.db) as db:
        count=ingest_log(db,args.log)
        print(f"Ingested {count} telemetry lines; unique records: {db.execute('SELECT COUNT(*) FROM weather_outbox').fetchone()[0]}")
