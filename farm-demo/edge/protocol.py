"""FPort v1 Protobuf -> normalized application JSON (not a radio or Cloud service)."""
import json
from google.protobuf.message import DecodeError
from weather_pb2 import WeatherRecord, WeatherEvent

PERIODIC_PORT, EVENT_PORT, ACK_PORT, CONFIG_PORT = 10, 11, 20, 21
MAX_PAYLOAD = 64
ALL_QUALITY = 127
ALL_STATUS = 31
RAIN_MM_PER_TIP = 0.2794

def require(ok, message):
    if not ok:
        raise ValueError(message)

def parse(cls, payload):
    require(isinstance(payload, bytes) and 0 < len(payload) <= MAX_PAYLOAD, "invalid payload length/type")
    msg = cls()
    try:
        msg.ParseFromString(payload)
    except DecodeError as exc:
        raise ValueError("malformed protobuf") from exc
    require(msg.IsInitialized(), "missing required fields")
    canonical = msg.SerializeToString(deterministic=True)
    msg.DiscardUnknownFields()
    require(msg.SerializeToString(deterministic=True) == canonical, "unknown fields for v1")
    require(msg.sequence_number > 0 and msg.ts_s > 0, "invalid sequence/time")
    return msg

def in_range(value, low, high):
    require(low <= value <= high, "measurement outside schema range")

def decode(fport, payload, device_id):
    """device_id must come from trusted transport mapping, not the payload."""
    require(isinstance(device_id, str) and bool(device_id.strip()), "missing device mapping")
    require(type(fport) is int, "invalid FPort")
    if fport == PERIODIC_PORT:
        msg = parse(WeatherRecord, payload)
        require(msg.period_s in (300, 900), "invalid period")
        require(not msg.status & ~ALL_STATUS and not msg.valid_mask & ~ALL_QUALITY, "unknown mask bits")
        result = dict(device_id=device_id, sequence_number=msg.sequence_number,
                      timestamp=msg.ts_s*1000, period_s=msg.period_s)
        fields = (
            (1, "temp_x10", "temperature", -400, 850, 10),
            (2, "humidity_x10", "humidity", 0, 1000, 10),
            (4, "pressure_x10", "pressure", 3000, 11000, 10),
            (8, "wind_x10", "wind_speed", 0, 0xffffffff, 10),
            (8, "gust_x10", "wind_gust", 0, 0xffffffff, 10),
            (16, "direction_deg", "wind_direction", 0, 359, 1),
            (32, "rain_tips", "rainfall", 0, 0xffffffff, None),
            (64, "battery_mv", "battery_voltage", 2000, 3650, 1000),
        )
        for bit, source, target, low, high, divisor in fields:
            valid = bool(msg.valid_mask & bit)
            require(msg.HasField(source) == valid, f"presence mismatch: {source}")
            if valid:
                value = getattr(msg, source)
                in_range(value, low, high)
                result[target] = round(value*RAIN_MM_PER_TIP, 4) if divisor is None else value if divisor == 1 else value/divisor
        result.update(status=msg.status, valid_mask=msg.valid_mask)
        return result
    if fport == EVENT_PORT:
        msg = parse(WeatherEvent, payload)
        require(msg.requested_period_s in (300, 900), "invalid requested period")
        require(not (msg.status|msg.changed_mask) & ~ALL_STATUS and
                not (msg.fault_mask|msg.changed_fault_mask) & ~ALL_QUALITY, "unknown event mask bits")
        result = dict(device_id=device_id, kind="event", sequence_number=msg.sequence_number,
                      timestamp=msg.ts_s*1000, changed_mask=msg.changed_mask, status=msg.status,
                      requested_period_s=msg.requested_period_s, fault_mask=msg.fault_mask,
                      changed_fault_mask=msg.changed_fault_mask, mode=WeatherEvent.Mode.Name(msg.mode),
                      buzzer=msg.buzzer, stop_request=msg.stop_request,
                      rain_10min_mm=round(msg.rain_10min_tips*RAIN_MM_PER_TIP, 4))
        for source,target,low,high,divisor in (
            ("wind_window_x10","wind_window_mps",0,0xffffffff,10),
            ("temp_x10","temperature",-400,850,10),
            ("battery_mv","battery_voltage",2000,3650,1000),
        ):
            if msg.HasField(source):
                value=getattr(msg,source)
                in_range(value,low,high)
                result[target]=value/divisor
        return result
    raise ValueError(f"unsupported FPort: {fport}")

def compact_json(record):
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
