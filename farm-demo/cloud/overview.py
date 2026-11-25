"""Read-only dashboard projection of native ThingsBoard data with human JWT."""
import math
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from provision import ApiError

VN = timezone(timedelta(hours=7))
GROUPS = (("temperature",), ("humidity",), ("pressure",), ("wind_speed","wind_gust"),
          ("wind_direction",), ("rainfall",), ("battery_voltage",))
KEYS = tuple(k for group in GROUPS for k in group)
DAY = 86400000

def number(value):
    if value is None or isinstance(value,bool):
        return None
    try:
        result=float(value)
        return result if math.isfinite(result) else None
    except (ValueError,TypeError):
        return None

def integer(value):
    n=number(value)
    return int(n) if n is not None and n.is_integer() else None

def attrs(api, path, scope):
    return {a["key"]:a["value"] for a in api.request("GET",path+"/values/attributes/"+scope)}

def point(data,key,stamp=None):
    for p in data.get(key,[]) or []:
        if (stamp is None or p.get("ts")==stamp) and p.get("value") is not None:
            return p["value"]
    return None

def readings(data, stamp):
    mask=integer(point(data,"valid_mask",stamp)) if stamp else None
    record=point(data,"record_id",stamp) if stamp else None
    result={}
    for i,group in enumerate(GROUPS):
        for key in group:
            value=number(point(data,key,stamp)) if record and mask is not None and mask & (1<<i) else None
            result[key]=value
    return result

def rain_and_trend(data, start, now):
    # Key-value history is joined by the actual periodic timestamp, never by list index.
    rows={}
    for key,points in data.items():
        for p in points or []:
            ts=p.get("ts")
            if isinstance(ts,int) and start <= ts <= now and p.get("value") is not None:
                rows.setdefault(ts,{})[key]=p["value"]
    midnight=int(datetime.fromtimestamp(now/1000,VN).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()*1000)
    total=Decimal(0)
    intervals=[]
    trend=[]
    seen=set()
    for ts,row in sorted(rows.items()):
        rid=row.get("record_id")
        period=integer(row.get("period_s"))
        mask=integer(row.get("valid_mask"))
        if not isinstance(rid,str) or rid in seen or period not in (300,900) or mask is None:
            continue
        seen.add(rid)
        temp=number(row.get("temperature")) if mask&1 else None
        trend.append({"ts":ts,"value":temp,"period_s":period})
        rain=number(row.get("rainfall")) if mask&32 else None
        begin=ts-period*1000
        # Rain is an interval total. A bucket crossing midnight cannot be split
        # without sub-interval measurements. Report known sum + coverage instead.
        if begin>=midnight and rain is not None and rain>=0:
            if intervals and begin<intervals[-1][1]:
                continue
            intervals.append((begin,ts))
            total+=Decimal(str(rain))
    coverage=sum(end-begin for begin,end in intervals)
    return {"total":float(total) if intervals else None,"coverage_s":coverage//1000,
            "elapsed_s":max(0,(now-midnight)//1000),"intervals":len(intervals)},trend

def overview(api, station, gateway, now):
    path="/api/plugins/telemetry/DEVICE/"+station
    gpath="/api/plugins/telemetry/DEVICE/"+gateway
    server=attrs(api,path,"SERVER_SCOPE")
    desired=attrs(api,path,"SHARED_SCOPE")
    applied=attrs(api,path,"CLIENT_SCOPE")
    period=integer(server.get("expected_period_s"))
    if period not in (300,900): period=300
    stamp=integer(server.get("farm_periodic_ts"))
    state_stamp=integer(server.get("farm_state_ts"))
    latest=api.request("GET",path+"/values/timeseries",
        keys=",".join(KEYS+("record_id","period_s","valid_mask","event_buzzer","event_stop_request","event_record_id")))
    vals=readings(latest,stamp)
    age=now-stamp if stamp and stamp<=now else None
    station_state="UNKNOWN" if age is None else ("STALE" if age>period*2000 else "FRESH")
    # The periodic cursor and state cursor are independent: an event must not
    # make old sensor measurements fresh.
    state_fresh=bool(state_stamp and 0<=now-state_stamp<=period*2000)
    start=now-DAY
    history=api.request("GET",path+"/values/timeseries",
        keys="record_id,period_s,valid_mask,temperature,rainfall",startTs=start,endTs=now+1,agg="NONE",limit=1000)
    rain,trend=rain_and_trend(history,start,now)
    gs=attrs(api,gpath,"SERVER_SCOPE")
    gts=integer(gs.get("farm_health_ts"))
    health=api.request("GET",gpath+"/values/timeseries",
        keys="pending,oldest_age_ms,disk_bytes,budget_bytes,link_state,radio_diagnostics")
    gateway_fresh=bool(gts and 0<=now-gts<=90000)
    def health_value(key):
        return point(health,key,gts) if gts else None
    import json
    radio=None
    raw=health_value("radio_diagnostics")
    if isinstance(raw,str):
        try:
            candidate=json.loads(raw).get(station)
            if isinstance(candidate,dict): radio=candidate
        except (ValueError,AttributeError):
            pass
    alarms=api.request("GET","/api/alarm/DEVICE/"+station,pageSize=100,page=0,searchStatus="ACTIVE")
    alarm_rows=[{"id":a["id"]["id"],"type":a["type"],"severity":a["severity"],
                 "acknowledged":bool(a.get("acknowledged")),"start":a.get("startTs")}
                for a in alarms["data"] if not a.get("cleared")]
    def confirmed_output(key):
        # Only a same-cursor event confirms output, never infer from alarm or RPC.
        if not state_fresh or not point(latest,"event_record_id",state_stamp): return None
        v=point(latest,key,state_stamp)
        return True if v is True or v=="true" else False if v is False or v=="false" else None
    return {"now":now,"station":station,"zone":server.get("zone_name"),
        "weather_ts":stamp,"state_ts":state_stamp,"freshness":station_state,"expected_period_s":period,
        "period_s":integer(point(latest,"period_s",stamp)) if stamp else None,
        "status":integer(server.get("farm_status")) if state_fresh else None,
        "valid_mask":integer(point(latest,"valid_mask",stamp)) if stamp else None,
        "values":vals,"rain_today":rain,"trend":trend,"alarms":alarm_rows,
        "buzzer":confirmed_output("event_buzzer"),"stop_request":confirmed_output("event_stop_request"),
        "desired":{k:desired.get(k) for k in ("config_version","wind_alarm_on","wind_alarm_off","report_period_s")},
        "applied":{k:applied.get(k) for k in ("applied_config_version","applied_wind_alarm_on","applied_wind_alarm_off","applied_report_period_s","applied_period_s","firmware_version")},
        "gateway":{"ts":gts,"fresh":gateway_fresh,"link_state":health_value("link_state"),
            "pending":integer(health_value("pending")),"oldest_age_ms":integer(health_value("oldest_age_ms")),
            "disk_bytes":integer(health_value("disk_bytes")),"budget_bytes":integer(health_value("budget_bytes")),
            "radio":radio}}
