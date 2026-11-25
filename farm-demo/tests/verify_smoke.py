"""Verify evidence produced by the actual STM32/Wokwi foundation scenario."""
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "edge"))
from outbox import connect, ingest_log

def require(condition, message):
    if not condition:
        raise ValueError(message)

def verify(log_path, db_path):
    log = Path(log_path).read_text(encoding="utf-8")
    markers = ("BOOT FARM_WS_01", "BME280_I2C_READY", "READY FARM_WS_01 SIMULATION", "WIND_ALARM_ON", "RAIN_TIPS=1", "WIND_ALARM_OFF", "TELEMETRY {")
    # Wokwi 0.29.1 may coalesce adjacent println messages in the saved serial
    # stream. Parse the complete ordered smoke grammar, not individual lines
    # or permissive substrings. Only whitespace is optional between tokens.
    obj = r"(\{[^{}\r\n]*\})"
    prefix = r"\s*".join(re.escape(marker) for marker in markers[:3])
    # Only recognized newer diagnostics are ignored; preserve strict business grammar.
    if "HARDWARE_ERROR" in log or "BUFFER_GAP" in log or "RPC_STORAGE_UNAVAILABLE" in log:
        raise ValueError("hardware failure in smoke trace")
    diagnostic = re.compile(
        r"FLASH_READY highwater=0 pending=0 corrupt=0|SIM_RTC offset=0 trusted=1|"
        r"BUFFER_STORED \d+ pending=\d+ used=\d+|"
        r"RADIO_TX seq=\d+ fcnt=\d+ fport=\d+ hex=[0-9a-f]+ delivered=[01] ts=\d+|"
        r"RX[12]|SIM_GATEWAY_ACK \d+|"
        r"ACK_FRAME ts=\d+ window=\d+ fport=\d+ hex=[0-9a-f]+|"
        r"DOWNLINK fport=20 rx=accepted count=1 pending=\d+")
    log=diagnostic.sub("",log)
    match = re.fullmatch(r"\s*" + prefix + r"\s*EVENT "+obj+r"\s*WIND_ALARM_ON\s*RAIN_TIPS=1\s*EVENT "+obj+r"\s*WIND_ALARM_OFF\s*TELEMETRY "+obj+r"\s*", log)
    require(match is not None, "unexpected, duplicate, missing or out-of-order smoke serial event")
    on, off, periodic = (json.loads(match.group(i)) for i in (1,2,3))
    for e, seq, status, mode in ((on,1,1,"ALARM"),(off,2,0,"AWAIT_INSPECTION")):
        require(e["device_id"]=="FARM_WS_01" and e["kind"]=="event" and
                type(e["sequence_number"]) is int and e["sequence_number"]==seq and
                e["changed_mask"]==1 and e["status"]==status and e["mode"]==mode and
                e["buzzer"] is bool(status) and e["stop_request"] is bool(status),
                "invalid wind event")
    require(on["wind_window_mps"]>=10 and off["wind_window_mps"]<7 and
            off["ts_s"]-on["ts_s"]>=60, "wrong wind transition conditions")
    records = [periodic]
    require(len(records) == 1, f"expected one complete periodic record, got {len(records)}")
    r = records[0]
    require(type(r["sequence_number"]) is int and type(r["period_s"]) is int
            and type(r["ts_s"]) is int and type(r["valid_mask"]) is int and type(r["status"]) is int,
            "integer telemetry metadata required")
    require(r["device_id"] == "FARM_WS_01", "unexpected station")
    require(r["sequence_number"] == 3 and r["period_s"] == 300, "wrong first record sequence or period")
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
        require(rows == [("FARM_WS_01", 3, r["ts_s"] * 1000, "PENDING")],
                "SQLite restart/replay verification failed")
    finally:
        db.close()
    return {"result": "PASS", "scope": "foundation smoke only", "simulated": True,
            "record_count": len(records), "unique_sqlite_records_after_replay": len(rows),
            "serial_markers": list(markers), "telemetry": r, "events": [on,off]}

if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify_smoke.py SERIAL_LOG DATABASE")
    print(json.dumps(verify(sys.argv[1], sys.argv[2]), indent=2, allow_nan=False))
