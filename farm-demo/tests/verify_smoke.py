"""Verify evidence produced by the actual STM32/Wokwi foundation scenario."""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "edge"))
from outbox import connect, ingest_log

def require(condition, message):
    if not condition:
        raise ValueError(message)

def verify(log_path, db_path):
    log = Path(log_path).read_text(encoding="utf-8")
    markers = ("READY FARM_WS_01 SIMULATION", "WIND_ALARM_ON", "RAIN_TIPS=1", "WIND_ALARM_OFF", "TELEMETRY {")
    lines = [line.strip() for line in log.splitlines()]
    position = -1
    for marker in markers:
        matches = [i for i, line in enumerate(lines) if (line.startswith(marker) if marker == "TELEMETRY {" else line == marker)]
        require(len(matches) == 1 and matches[0] > position, f"missing, duplicate or out-of-order serial marker: {marker}")
        position = matches[0]
    records = [json.loads(line.split("TELEMETRY ", 1)[1])
               for line in log.splitlines() if "TELEMETRY {" in line]
    require(len(records) == 1, f"expected one complete periodic record, got {len(records)}")
    r = records[0]
    require(type(r["sequence_number"]) is int and type(r["period_s"]) is int
            and type(r["ts_s"]) is int and type(r["valid_mask"]) is int and type(r["status"]) is int,
            "integer telemetry metadata required")
    require(r["device_id"] == "FARM_WS_01", "unexpected station")
    require(r["sequence_number"] == 1 and r["period_s"] == 300, "wrong first record sequence or period")
    require(r["ts_s"] == 1795453500, "wrong simulated measurement timestamp")
    require(r["valid_mask"] == 127, "unexpected validity mask")
    require(r["status"] == 0, "unexpected alarm status after recovery")
    require(math.isclose(r["rainfall"], 0.2794, abs_tol=1e-5), "rainfall does not match one accepted tip")
    require(r["wind_gust"] >= 10 and r["wind_gust"] >= r["wind_speed"], "wind gust was not recorded correctly")
    for key, low, high in (("temperature", 10, 50), ("humidity", 0, 100),
                           ("pressure", 900, 1100), ("battery_voltage", 2.7, 3.6),
                           ("wind_speed", 0, 20), ("wind_gust", 0, 20),
                           ("wind_direction", 0, 359)):
        value = r[key]
        require(type(value) in (float, int) and math.isfinite(value) and low <= value <= high,
                f"invalid {key}: {value}")
    db = connect(db_path)
    try:
        require(ingest_log(db, log_path) == len(records), "first ingest count mismatch")
        require(db.execute("SELECT COUNT(*) FROM weather_outbox").fetchone()[0] == len(records), "first unique count mismatch")
    finally:
        db.close()
    db = connect(db_path)
    try:
        ingest_log(db, log_path)
        rows = db.execute("SELECT device_id, sequence_number, ts_ms, state FROM weather_outbox").fetchall()
        require(rows == [("FARM_WS_01", 1, r["ts_s"] * 1000, "PENDING")],
                "SQLite restart/replay verification failed")
    finally:
        db.close()
    return {"result": "PASS", "scope": "foundation smoke only", "simulated": True,
            "record_count": len(records), "unique_sqlite_records_after_replay": len(rows),
            "serial_markers": list(markers), "telemetry": r}

if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify_smoke.py SERIAL_LOG DATABASE")
    print(json.dumps(verify(sys.argv[1], sys.argv[2]), indent=2, allow_nan=False))
