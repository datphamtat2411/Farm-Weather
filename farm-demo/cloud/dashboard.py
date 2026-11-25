"""Install the versioned native ThingsBoard dashboard and its tenant widget."""
import argparse
import json
import os
import uuid
from pathlib import Path
from provision import Rest, ApiError, provision
from security import uid, assign, members

TITLE="FarmWeather | Giám sát trang trại"
FQN="farm_weather_overview_v1"
ROOT=Path(__file__).with_name("dashboard")
WIDGET_ID="5d01fd01-8943-48f1-9b4f-397b8a8c46ec"

def install(api, topology, manifest):
    descriptor={"type":"static","sizeX":24,"sizeY":24,"resources":[],
        "templateHtml":(ROOT/"overview.html").read_text(),
        "templateCss":(ROOT/"overview.css").read_text(),
        "controllerScript":(ROOT/"overview.js").read_text(),
        "settingsSchema":"{}","dataKeySettingsSchema":"{}",
        "defaultConfig":json.dumps({"datasources":[],"showTitle":False,"padding":"0px",
          "dropShadow":False,"settings":{},"backgroundColor":"#f3f6f4"})}
    widget={"fqn":FQN,"name":"FarmWeather overview v1","description":"Farm scoped live monitoring and safe controls","descriptor":descriptor}
    try:
        existing=api.request("GET","/api/widgetType",fqn="tenant."+FQN)
        widget["id"]=existing["id"]
    except ApiError as e:
        if e.status!=404: raise
    saved=api.request("POST","/api/widgetType",widget)
    if saved["fqn"]!=FQN: raise ValueError("widget FQN changed")
    config={"widgets":{WIDGET_ID:{"id":WIDGET_ID,"typeFullFqn":"tenant."+FQN,"type":"static",
        "sizeX":24,"sizeY":24,"config":{"datasources":[],"showTitle":False,"padding":"0px","dropShadow":False,
        "backgroundColor":"#f3f6f4","settings":{"stations":[{"id":v,"name":k} for k,v in sorted(topology["devices"].items())]}}}},
        "states":{"default":{"name":"Tổng quan","root":True,"layouts":{"main":{
            "widgets":{WIDGET_ID:{"sizeX":24,"sizeY":24,"row":0,"col":0,"mobileOrder":1,"mobileHeight":36}},
            "gridSettings":{"columns":24,"margin":0,"outerMargin":False,"autoFillHeight":True,
                "mobileAutoFillHeight":True,"mobileRowHeight":70,"layoutType":"default","backgroundColor":"#f3f6f4"}}}}},
        "entityAliases":{},"filters":{},"settings":{"showTitle":False,"showDashboardsSelect":False}}
    group_id=manifest["dashboard_group"]
    existing_ids=members(api,group_id)
    if len(existing_ids)>1: raise ValueError("unexpected farm dashboards")
    dashboard={"title":TITLE,"configuration":config,"mobileHide":False}
    if existing_ids:
        existing=api.request("GET","/api/dashboard/"+next(iter(existing_ids)))
        if existing["title"]!=TITLE: raise ValueError("refusing to overwrite unrelated dashboard")
        dashboard={**existing,**dashboard}
    else:
        other=api.named("/api/tenant/dashboards",TITLE,"title")
        if other:
            dashboard={**api.request("GET","/api/dashboard/"+uid(other)),**dashboard}
            owner=(dashboard.get("customerId") or {}).get("id")
            if owner not in (None,"13814000-1dd2-11b2-8080-808080808080","00000000-0000-0000-0000-000000000000",manifest["customer"]):
                raise ValueError("dashboard belongs to another customer")
    result=api.request("POST","/api/dashboard",dashboard)
    assign(api,{"id":{"id":manifest["customer"]}},"DASHBOARD",uid(result),{"id":{"id":group_id}})
    if members(api,group_id)!={uid(result)}: raise ValueError("dashboard group mismatch")
    return uid(result)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url",required=True)
    p.add_argument("--ca-file")
    p.add_argument("--manifest",type=Path,required=True)
    a=p.parse_args()
    api=Rest(a.url,a.ca_file)
    api.login(os.environ["FARM_TB_ADMIN_USER"],os.environ["FARM_TB_ADMIN_PASSWORD"])
    topology,_=provision(api)
    manifest=json.loads(a.manifest.read_text())
    if set(topology["devices"].values())!=set(manifest["station_ids"]) or topology["gateway"]!=manifest["gateway"]:
        raise ValueError("manifest topology mismatch")
    ident=install(api,topology,manifest)
    print("Dashboard:",a.url.rstrip("/")+"/dashboards/"+ident)

if __name__=="__main__":
    main()
