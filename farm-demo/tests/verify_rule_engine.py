"""Task 11 integration against the actual ThingsBoard rule engine and alarms."""
import argparse
import json
import time
from pathlib import Path
from verify_thingsboard import wait_login, STATION, MqttRestCloud, provision
from rules import ALARM_TYPES, BUFFER

def eventually(check, description, seconds=25):
    deadline=time.monotonic()+seconds
    last=None
    while time.monotonic()<deadline:
        last=check()
        if last:
            return last
        time.sleep(.3)
    raise AssertionError(description)

def run(url, ca, port):
    api=wait_login(url,ca)
    topology, token=provision(api)
    station=topology["devices"][STATION]
    gateway=topology["gateway"]
    adapter=MqttRestCloud("127.0.0.1",port,url,topology["devices"],token=token,
        api_token=api.authorization.removeprefix("Bearer "),ca_file=ca,timeout=10)
    adapter.connect()
    stamp=int(time.time()*1000)-30000
    seq=1000
    evidence=[]
    names={name:severity for name,severity,_ in ALARM_TYPES}
    def attributes(device):
        return {x["key"]:x["value"] for x in api.request("GET",
            f"/api/plugins/telemetry/DEVICE/{device}/values/attributes/SERVER_SCOPE")}
    def alarms(device, active=True):
        return api.request("GET",f"/api/alarm/DEVICE/{device}",pageSize=100,page=0,
                           searchStatus="ACTIVE" if active else "ANY")["data"]
    def active(device):
        return {x["type"]:x for x in alarms(device)}
    def expect(device, expected):
        def check():
            actual=active(device)
            return actual if set(actual)==set(expected) and all(actual[k]["severity"]==v for k,v in expected.items()) else False
        if expected:
            return eventually(check, f"active alarms mismatch: {expected}")
        eventually(lambda:not active(device),"alarms not cleared")
        return {}
    def send(status, *, period=300, history=False, event=False, ts=None, number=None):
        nonlocal seq,stamp
        if number is None:
            seq+=1
            number=seq
        if ts is None:
            stamp+=1
            ts=stamp
        v={"sync_mode":"history" if history else "live"}
        if event:
            v.update(event_record_id=f"{STATION}:{number}",event_sequence_number=number,
                     event_status=status,event_requested_period_s=period,
                     event_changed_mask=31,event_buzzer=bool(status&1),event_stop_request=bool(status&1))
        else:
            v.update(record_id=f"{STATION}:{number}",sequence_number=number,status=status,
                     period_s=period,valid_mask=127,temperature=29.5,humidity=78.2,
                     pressure=1008.4,wind_speed=6.3,wind_gust=8.8,
                     wind_direction=135,rainfall=2.5146,battery_voltage=3.2)
        packet={STATION:[{"ts":ts,"values":v}]}
        adapter.publish(packet)
        eventually(lambda:adapter.read(packet).get((STATION,ts),{}).get(
            "event_record_id" if event else "record_id")==f"{STATION}:{number}","native history write")
        return number,ts,packet
    # Use the actual server scheduler; accelerate only the trigger frequency
    # in the disposable CI instance. No telemetry heartbeat or station token.
    scheduler_id=topology["rule_chains"]["watchdog"]
    scheduler=api.request("GET","/api/schedulerEvent/"+scheduler_id)
    scheduler["schedule"]["startTime"]=int(time.time()*1000)+2000
    scheduler["schedule"]["repeat"]["repeatInterval"]=2
    api.request("POST","/api/schedulerEvent",scheduler)
    def tick():
        time.sleep(3)
    def health(percent):
        adapter.publish_health(dict(disk_bytes=percent,budget_bytes=100,pending=2,
            oldest_age_ms=1000,state="ONLINE"),int(time.time()*1000))
    def set_age(age_ms, period):
        api.request("POST",f"/api/plugins/telemetry/DEVICE/{station}/SERVER_SCOPE",
                    {"farm_state_ts":int(time.time()*1000)-age_ms,"expected_period_s":period})
    try:
        send(0)
        expect(station,{})
        n,t,p=send(31,event=True,period=900)
        expected={k:v for k,v in names.items() if k!="DATA_STALE"}
        a=expect(station,expected)
        wind=a["WIND_HIGH"]["id"]["id"]
        api.request("POST",f"/api/alarm/{wind}/ack")
        eventually(lambda:active(station).get("WIND_HIGH",{}).get("acknowledged"),"ack is not clear")
        adapter.publish(p)
        # A late normal record must not clear the active alarms or change latest weather.
        send(0,history=True,ts=t-1000)
        # Repeated confirmed state updates the existing active alarm; it is not a new incident.
        send(31,event=True,period=900)
        assert active(station)["WIND_HIGH"]["id"]["id"]==wind
        assert active(station)["WIND_HIGH"]["acknowledged"]
        assert len([x for x in alarms(station,False) if x["type"]=="WIND_HIGH" and not x["cleared"]])==1
        assert attributes(station)["expected_period_s"]==900
        evidence.append({"case":"five native alarm types + acknowledge + replay","result":"PASS","alarm_ids":{k:v["id"]["id"] for k,v in a.items()}})
        send(0,period=300)
        expect(station,{})
        assert any(x["id"]["id"]==wind and x["cleared"] for x in alarms(station,False))
        assert len([x for x in alarms(station,False) if x["type"]=="WIND_HIGH" and x["id"]["id"]==wind])==1
        # Old alarm event marked live by a delayed/replayed transport still cannot
        # overrule the durable sequence/timestamp watermark.
        send(31,event=True,ts=t-2000,number=900)
        expect(station,{})
        evidence.append({"case":"clear and late live replay cannot re-raise","result":"PASS"})
        # Inject only the persisted measurement age, not a fake Alarm. Exercise
        # actual rule nodes without sleeping 30 minutes.
        set_age(590000,300); tick(); time.sleep(1); expect(station,{})
        set_age(601000,300); tick(); expect(station,{"DATA_STALE":"MAJOR"})
        send(0); expect(station,{})
        set_age(1790000,900); tick(); time.sleep(1); expect(station,{})
        set_age(1801000,900); tick(); expect(station,{"DATA_STALE":"MAJOR"})
        send(0,period=900); expect(station,{})
        evidence.append({"case":"stale watchdog at 10/30 minutes and fresh recovery","result":"PASS","time_acceleration":"server-side measurement watermark aged in disposable test"})
        health(79); time.sleep(1); expect(gateway,{})
        health(80); expect(gateway,{BUFFER[0]:BUFFER[1]})
        health(70); time.sleep(1); expect(gateway,{BUFFER[0]:BUFFER[1]})
        health(69); expect(gateway,{})
        evidence.append({"case":"gateway buffer hysteresis 79/80/70/69","result":"PASS"})
        # Query the real latest and historical timeseries; routing/tick keys
        # must never be exposed as measurements.
        path=f"/api/plugins/telemetry/DEVICE/{station}/values/timeseries"
        actual=api.request("GET",path,keys="record_id,temperature,sync_mode,sync_state,farm_watchdog")
        assert not any(actual.get(k) for k in ("sync_mode","sync_state","farm_watchdog"))
        assert actual["record_id"][0]["value"]==f"{STATION}:{seq}"
        for key in ("sync_mode","sync_state","farm_watchdog"):
            historical=api.request("GET",path,keys=key,startTs=stamp-3600000,endTs=int(time.time()*1000)+1000,agg="NONE",limit=1000)
            assert not historical.get(key)
        evidence.append({"case":"latest/history routing metadata excluded","result":"PASS"})
        # Actual native Notification Center web notifications, not log strings.
        notices=eventually(lambda: [x for x in api.request("GET","/api/notifications",
            pageSize=100,page=0,deliveryMethod="WEB")["data"] if
            str(x.get("subject","")).startswith("Farm weather:")], "native web notification")
        assert sum(x.get("subject")=="Farm weather: WIND_HIGH" for x in notices)==1, "duplicate wind notification"
        evidence.append({"case":"native web notifications","result":"PASS","count":len(notices)})
        return {"result":"PASS","rule_chains":topology["rule_chains"],"checks":evidence}
    finally:
        adapter.close()

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--url",default="https://127.0.0.1:8443")
    p.add_argument("--ca",required=True)
    p.add_argument("--port",type=int,default=8883)
    a=p.parse_args()
    print(json.dumps(run(a.url,a.ca,a.port),indent=2))
