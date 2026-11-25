"""Scoped periodic history; interval totals are never interpolated across boundaries."""
import csv
import io
import math
from datetime import datetime
from decimal import Decimal
from overview import GROUPS, KEYS, VN, number, integer

DAY = 86400000
HOUR = 3600000
META = ("record_id", "sequence_number", "period_s", "valid_mask", "status")
HISTORY_KEYS = ",".join(META + KEYS)

def fetch_points(api, station, start, end):
    """Bound requests by seven days (2016 regular five-minute records)."""
    path = f"/api/plugins/telemetry/DEVICE/{station}/values/timeseries"
    points = {}
    for left in range(start, end, 7*DAY):
        right = min(left+7*DAY, end)
        page = api.request("GET", path, keys=HISTORY_KEYS, startTs=max(0,left-1),
                           endTs=right+1, agg="NONE", limit=2500)
        for key, entries in page.items():
            if key not in META + KEYS:
                continue
            if len(entries) >= 2500:
                raise ValueError("history query limit reached")
            points.setdefault(key, []).extend(p for p in entries if left < p["ts"] <= right)
    return points

def records(points, start, end):
    joined = {}
    for key, entries in points.items():
        if key not in META + KEYS:
            continue
        for p in entries:
            ts = p.get("ts")
            if type(ts) is int and start < ts <= end and p.get("value") is not None:
                row = joined.setdefault(ts, {})
                if key in row and row[key] != p["value"]:
                    raise ValueError("conflicting history point")
                row[key] = p["value"]
    rows, seen = [], {}
    for ts, raw in sorted(joined.items()):
        rid = raw.get("record_id")
        seq = integer(raw.get("sequence_number"))
        period = integer(raw.get("period_s"))
        mask = integer(raw.get("valid_mask"))
        if not isinstance(rid,str) or not rid or seq is None or seq <= 0 or not rid.endswith(":"+str(seq)):
            continue
        if period not in (300,900) or mask is None or not 0 <= mask <= 127:
            continue
        vals = {k: number(raw.get(k)) if mask & (1<<i) else None
                for i,group in enumerate(GROUPS) for k in group}
        for k in ("rainfall","wind_speed","wind_gust","battery_voltage"):
            if vals[k] is not None and vals[k]<0:
                vals[k]=None
        if vals["wind_direction"] is not None and not 0<=vals["wind_direction"]<360:
            vals["wind_direction"]=None
        if rid in seen:
            # A retry retains its original timestamp. Conflicting identities
            # cannot be counted twice or arbitrarily resolved.
            if seen[rid] != ts:
                raise ValueError("record identity reused at another timestamp")
            continue
        seen[rid]=ts
        rows.append({"ts":ts,"record_id":rid,"sequence_number":seq,"period_s":period,
                     "valid_mask":mask,"status":integer(raw.get("status")),"values":vals})
    return rows

def select_periods(rows, start, end):
    """Choose complete, non-overlapping periods in timestamp order."""
    accepted, boundary, overlap = [], 0, 0
    last_end = start
    for row in rows:
        ts = row["ts"]
        if not start < ts <= end:
            continue
        begin = ts-row["period_s"]*1000
        if begin < start:
            boundary += 1
            continue
        if begin < last_end:
            overlap += 1
            continue
        accepted.append(row)
        last_end = ts
    return accepted, boundary, overlap

def aggregate(rows, start, end):
    accepted, boundary, overlap = select_periods(rows, start, end)
    duration = (end-start)/1000
    stats = {}
    for key in KEYS:
        pairs = [(r["values"][key],r["period_s"]) for r in accepted if r["values"][key] is not None]
        seconds = sum(p for _,p in pairs)
        vals = [v for v,_ in pairs]
        s = {"count":len(vals),"coverage_s":seconds,"coverage_pct":100*seconds/duration,
             "min":min(vals) if vals else None,"max":max(vals) if vals else None,
             "avg":None,"last":vals[-1] if vals else None}
        if vals and key not in ("rainfall","wind_direction","wind_gust"):
            s["avg"] = sum(v*p for v,p in pairs)/seconds if key=="wind_speed" else sum(vals)/len(vals)
        if key=="rainfall":
            s["total"]=float(sum((Decimal(str(v)) for v in vals),Decimal(0))) if vals else None
        if key=="wind_direction":
            x=sum(math.cos(math.radians(v))*p for v,p in pairs)
            y=sum(math.sin(math.radians(v))*p for v,p in pairs)
            strength=math.hypot(x,y)/seconds if seconds else None
            s["vector_deg"]=math.degrees(math.atan2(y,x))%360 if strength is not None and strength>=0.2 else None
            s["vector_strength"]=strength
            s["min"]=s["max"]=None
        stats[key]=s
    coverage=sum(r["period_s"] for r in accepted)
    full=sum(r["period_s"] for r in accepted if all(r["values"][k] is not None for k in KEYS))
    gaps=[]
    cursor=start
    for r in accepted:
        begin=r["ts"]-r["period_s"]*1000
        if cursor<begin:
            gaps.append({"startTs":cursor,"endTs":begin})
        cursor=r["ts"]
    if cursor<end:
        gaps.append({"startTs":cursor,"endTs":end})
    return {"metrics":stats,"coverage_s":coverage,"valid_coverage_s":full,
            "coverage_pct":100*coverage/duration,"valid_coverage_pct":100*full/duration,
            "boundary_periods":boundary,"overlapping_periods":overlap,"gaps":gaps,
            "count":len(accepted)}

def report(rows, start, end):
    # UTC+7 has no DST. Align daily boundaries to Vietnam midnight.
    step=HOUR if end-start<=DAY else DAY
    offset=7*HOUR
    cursor=start
    buckets=[]
    accepted, _, _ = select_periods(rows, start, end)
    while cursor<end:
        right=min(end,((cursor+offset)//step+1)*step-offset)
        bucket=aggregate(accepted,cursor,right)
        buckets.append({"startTs":cursor,"endTs":right,**bucket})
        cursor=right
    return {"startTs":start,"endTs":end,"bucket_s":step//1000,"timezone":"Asia/Ho_Chi_Minh",
            "summary":aggregate(rows,start,end),"buckets":buckets,"records":rows}

def alarm_count(api, station, start, end):
    """Count alarm starts, not repeated telemetry or acknowledgements."""
    page=0
    seen=set()
    while True:
        result=api.request("GET",f"/api/alarm/DEVICE/{station}",pageSize=100,page=page,
                           startTime=start,endTime=end,sortProperty="createdTime",sortOrder="ASC")
        for alarm in result["data"]:
            ts=alarm.get("startTs")
            if type(ts) is int and start<ts<=end:
                seen.add(alarm["id"]["id"])
        if not result["hasNext"]:
            return len(seen)
        page+=1
        if page>=1000:
            raise ValueError("alarm query limit reached")

def history(api, station, start, end):
    rows=records(fetch_points(api,station,start,end),start,end)
    result=report(rows,start,end)
    result["alarm_count"]=alarm_count(api,station,start,end)
    return result

def csv_report(rows):
    output=io.StringIO(newline="")
    writer=csv.writer(output)
    writer.writerow(["timestamp_ms_utc","timestamp_utc_plus_7","record_id","sequence_number",
                     "period_s","valid_mask","status",*KEYS])
    for r in rows:
        rid=r["record_id"]
        # Spreadsheet formula injection protection even if an upstream ID is malformed.
        if rid.startswith(("=","+","-","@","\t","\r","\n")):
            rid="'"+rid
        writer.writerow([r["ts"],datetime.fromtimestamp(r["ts"]/1000,VN).isoformat(),rid,
                         r["sequence_number"],r["period_s"],r["valid_mask"],r["status"],
                         *(r["values"][k] for k in KEYS)])
    return output.getvalue()
