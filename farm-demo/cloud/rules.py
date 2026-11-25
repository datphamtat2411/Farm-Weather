"""Task 11: provision native, scoped ThingsBoard rule chains via REST."""
import json
import time
from pathlib import Path
from provision import ApiError, ensure_attributes, STATION, GATEWAY

ROOT = Path(__file__).resolve().parent
PREFIX = "org.thingsboard.rule.engine."
STATION_CHAIN = "Farm weather station v1"
GATEWAY_CHAIN = "Farm edge health v1"
ALARM_TYPES = [
    ("WIND_HIGH", "CRITICAL", 1), ("RAIN_HIGH", "MAJOR", 2),
    ("TEMPERATURE_HIGH", "MAJOR", 4), ("BATTERY_LOW", "WARNING", 8),
    ("SENSOR_FAULT", "MAJOR", 16), ("DATA_STALE", "MAJOR", 0),
]
BUFFER = ("BUFFER_NEAR_FULL", "WARNING", 0)
ON = {"type": "ON_EVERY_MESSAGE"}
SKIP = {"type": "SKIP"}
STATE_KEYS = ["farm_state_seq", "farm_state_ts", "farm_status", "farm_periodic_seq",
              "farm_periodic_ts", "expected_period_s", "farm_monitor_since",
              "farm_health_ts", "farm_buffer_warning"]

def js_config(script):
    return {"scriptLang": "JS", "jsScript": script, "tbelScript": ""}

class Graph:
    def __init__(self):
        self.nodes, self.links = [], []
    def node(self, kind, name, config, version=0, x=0, y=0):
        idx = len(self.nodes)
        self.nodes.append(dict(type=PREFIX+kind, name=name, configuration=config,
            configurationVersion=version, debugFailures=True, debugAll=False,
            additionalInfo={"layoutX": x, "layoutY": y}))
        return idx
    def link(self, src, dst, label="Success"):
        self.links.append(dict(fromIndex=src, toIndex=dst, type=label))
    def transform(self, name, script, x, y):
        return self.node("transform.TbTransformMsgNode", name, js_config(script), x=x, y=y)
    def switch(self, name, script, x, y):
        return self.node("filter.TbJsSwitchNode", name, js_config(script), x=x, y=y)
    def save(self, name, history, x, y):
        settings = {"type": "ADVANCED", "timeseries": ON, "latest": SKIP,
                    "webSockets": SKIP, "calculatedFields": SKIP} if history else ON
        return self.node("telemetry.TbMsgTimeseriesNode", name,
                         {"defaultTTL": 0, "useServerTs": False, "processingSettings": settings}, 1,x,y)

def ensure_named(api, path, name, body):
    existing = api.named(path+"s", name)
    if existing:
        return existing
    return api.request("POST", path, dict(name=name, **body))

def notifications(api):
    target = ensure_named(api, "/api/notification/target", "Farm weather supervisors",
        {"configuration": {"type": "PLATFORM_USERS", "usersFilter": {"type": "TENANT_ADMINISTRATORS"}}})
    template = ensure_named(api, "/api/notification/template", "Farm weather alarm created",
        {"notificationType": "RULE_NODE", "configuration": {"deliveryMethodsTemplates": {
            "WEB": {"method": "WEB", "enabled": True, "subject": "Farm weather: ${type}",
                    "body": "${deviceName}: ${type} (${severity}). Check the alarm in ThingsBoard."}}}})
    return {"targets": [target["id"]["id"]], "templateId": template["id"]}

def graph(chain_id, root_id, notify, *, gateway=False):
    g = Graph()
    entry = g.node("filter.TbMsgTypeSwitchNode", "Message Type Switch", {}, x=180,y=220)
    passthrough = g.node("flow.TbRuleChainInputNode", "Platform default non-telemetry",
                         {"ruleChainId": root_id, "forwardMsgToDefaultRuleChain": False}, x=400,y=60)
    for label in ("Post attributes", "RPC Request from Device", "RPC Request to Device", "Other"):
        g.link(entry, passthrough,label)
    fetch = g.node("metadata.TbGetAttributesNode", "Read durable originator state",
        {"clientAttributeNames": [], "sharedAttributeNames": [], "serverAttributeNames": STATE_KEYS,
         "latestTsKeyNames": [], "tellFailureIfAbsent": False, "getLatestValueWithTs": False,
         "fetchTo": "METADATA"}, 1,400,220)
    g.link(entry,fetch,"Post telemetry")
    classify = g.transform("Validate and classify", (ROOT/"scripts/classify.js").read_text().replace(
        "__GATEWAY__", "true" if gateway else "false"),620,220)
    g.link(fetch,classify)
    route = g.switch("Live / history / watchdog / health / invalid", "return [metadata.farm_route];",840,220)
    g.link(classify,route)
    historic = g.save("Save history ONLY",True,1060,60)
    current = g.save("Save current + history + WebSocket",False,1060,160)
    reject = g.node("action.TbLogNode", "Reject malformed farm message",
        js_config("return 'Farm invalid telemetry: ' + metadata.farm_reason;"), x=1060,y=420)
    g.link(route,historic,"history")
    g.link(route,current,"latest")
    g.link(route,reject,"invalid")
    # Native alarms are processed serially within the originator queue; restore the
    # original payload after action nodes return an ALARM message.
    start = route
    start_labels = ["health"] if gateway else ["state", "tick"]
    for index,(name,severity,bit) in enumerate([BUFFER] if gateway else ALARM_TYPES):
        x=1080+index*300
        if name=="DATA_STALE":
            condition = "if (metadata.farm_route === 'tick') return [metadata.farm_stale === 'true' ? 'on' : 'skip']; return ['off'];"
        elif name=="BUFFER_NEAR_FULL":
            condition = "return [metadata.farm_buffer === 'true' ? 'on' : 'off'];"
        else:
            condition = f"if (metadata.farm_route === 'tick') return ['skip']; return [(Number(metadata.farm_status) & {bit}) !== 0 ? 'on' : 'off'];"
        switch = g.switch(name+" decision",condition,x,540)
        for label in start_labels:
            g.link(start,switch,label)
        details = ("return {recordId: metadata.farm_record_id || '', measurementTs: Number(metadata.farm_ts), "
                   "confirmedStatus: Number(metadata.farm_status || 0), "
                   "source: metadata.farm_route, snapshot: JSON.parse(metadata.farm_original)};")
        cfg = dict(scriptLang="JS", alarmDetailsBuildJs=details, alarmDetailsBuildTbel="", alarmType=name)
        create = g.node("action.TbCreateAlarmNode", "Create/update "+name,dict(cfg,severity=severity,
            propagate=False, propagateToOwner=False, propagateToOwnerHierarchy=False,
            propagateToTenant=False, useMessageAlarmData=False, overwriteAlarmDetails=True,
            dynamicSeverity=False, relationTypes=[]),x=x+150,y=470)
        clear = g.node("action.TbClearAlarmNode","Clear "+name,cfg,x=x+150,y=650)
        n = g.node("notification.TbNotificationNode","Notify new "+name,notify,x=x+150,y=350)
        restore = g.transform("Continue "+name,"return {msg: JSON.parse(metadata.farm_original), metadata: metadata, msgType: 'POST_TELEMETRY_REQUEST'};",x+280,540)
        g.link(switch,create,"on");g.link(switch,clear,"off");g.link(switch,restore,"skip")
        g.link(create,n,"Created")
        for label in ("Updated","False"):
            g.link(create,restore,label)
        for label in ("Cleared","False"):
            g.link(clear,restore,label)
        g.link(n,restore)
        start,start_labels=restore,["Success"]
    # Tick must not update the measurement watermark. Health has its own cursor.
    tail = g.switch("Persist state only for accepted records","return [metadata.farm_route === 'tick' ? 'tick' : 'persist'];",3000,780)
    g.link(start,tail)
    state = g.transform("Build server-side current state", (ROOT/"scripts/state.js").read_text(),3260,780)
    g.link(tail,state,"persist")
    if not gateway:
        g.link(route,state,"periodic")
    attr = g.node("telemetry.TbMsgAttributesNode","Persist confirmed state", {
        "processingSettings":ON,"scope":"SERVER_SCOPE","notifyDevice":False,
        "sendAttributesUpdatedNotification":False,"updateAttributesOnlyOnValueChange":True},3,3500,780)
    g.link(state,attr)
    restore = g.transform("Restore clean telemetry","return {msg: JSON.parse(metadata.farm_original), metadata: metadata, msgType: 'POST_TELEMETRY_REQUEST'};",3740,780)
    g.link(attr,restore)
    final = g.switch("Save accepted telemetry","return [metadata.farm_save];",3980,780)
    g.link(restore,final)
    g.link(final,current,"latest");g.link(final,historic,"history")
    return dict(ruleChainId=chain_id, firstNodeIndex=entry,nodes=g.nodes,connections=g.links)

def install(api, topology):
    root = api.named("/api/ruleChains", "Root Rule Chain")
    if not root or not root["root"]:
        raise ValueError("expected unmodified platform root chain")
    queue = api.request("GET", "/api/queues/name/SequentialByOriginator")
    if queue["submitStrategy"]["type"] != "SEQUENTIAL_BY_ORIGINATOR":
        raise ValueError("farm state requires serialized originator processing")
    notify = notifications(api)
    result = {}
    for key, name, is_gateway in [("station_profile", STATION_CHAIN, False),
                                  ("gateway_profile", GATEWAY_CHAIN, True)]:
        chain = api.named("/api/ruleChains", name)
        if chain is None:
            chain = api.request("POST","/api/ruleChain",{"name":name,"root":False,"type":"CORE"})
        chain_id = chain["id"]
        meta = graph(chain_id,root["id"]["id"],notify,gateway=is_gateway)
        old = api.request("GET","/api/ruleChain/"+chain_id["id"]+"/metadata")
        # Preserve node UUIDs on identical re-provisioning. Refuse unknown edits.
        if old.get("nodes"):
            def same_graph():
                if len(old["nodes"]) != len(meta["nodes"]):
                    return False
                for a,b in zip(old["nodes"],meta["nodes"]):
                    if a["name"] != b["name"] or a["type"] != b["type"]:
                        return False
                    if any(a["configuration"].get(k)!=v for k,v in b["configuration"].items()):
                        return False
                key = lambda l: (l["fromIndex"],l["toIndex"],l["type"])
                return sorted(map(key,old["connections"])) == sorted(map(key,meta["connections"]))
            if not same_graph():
                raise ValueError("managed rule chain differs; migrate explicitly rather than overwrite")
        else:
            api.request("POST","/api/ruleChain/metadata",meta)
        profile = api.request("GET","/api/deviceProfile/"+topology[key])
        if profile.get("defaultRuleChainId") != chain_id or profile.get("defaultQueueName") != queue["name"]:
            profile.update(defaultRuleChainId=chain_id,defaultQueueName=queue["name"])
            api.request("POST","/api/deviceProfile",profile)
        result[key]=chain_id["id"]
    station={"id":{"id":topology["devices"][STATION],"entityType":"DEVICE"}}
    ensure_attributes(api,station,"SERVER_SCOPE",{"farm_monitor_since":int(time.time()*1000)})
    return result
