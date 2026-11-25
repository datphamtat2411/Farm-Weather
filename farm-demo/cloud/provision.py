"""Idempotent ThingsBoard provisioning via official REST APIs; no SQL writes."""
import argparse
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATION = "FARM_WS_01"
GATEWAY = "FARM_EDGE_01"
FARM = "Farm Weather Demo"
ZONE = "Cultivation Zone 01"

class ApiError(RuntimeError):
    def __init__(self, status, method, path):
        super().__init__(f"ThingsBoard HTTP {status}: {method} {path.split('?')[0]}")
        self.status = status

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Rest:
    def __init__(self, url, ca_file=None, authorization=None, timeout=10):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("HTTPS endpoint required")
        self.url = url.rstrip("/")
        self.ca_file = ca_file
        self.context = ssl.create_default_context(cafile=ca_file)
        self.authorization = authorization
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=self.context), NoRedirect())

    def request(self, method, path, data=None, **query):
        suffix = "?" + urllib.parse.urlencode(query) if query else ""
        headers = {"Accept": "application/json"}
        if self.authorization:
            headers["X-Authorization"] = self.authorization
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data, allow_nan=False).encode()
        request = urllib.request.Request(self.url + path + suffix, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            # Do not log response body, password, JWT or access token.
            raise ApiError(error.code, method, path) from None

    def login(self, username, password):
        self.authorization = "Bearer " + self.request("POST", "/api/auth/login",
            {"username": username, "password": password})["token"]

    def named(self, path, name, field="name"):
        page = 0
        while True:
            response = self.request("GET", path, pageSize=100, page=page, textSearch=name)
            matches = [item for item in response["data"] if item.get(field) == name]
            if matches:
                return matches[0]
            if not response["hasNext"]:
                return None
            page += 1

def ensure_profile(api, name):
    profile = api.named("/api/deviceProfiles", name)
    if profile is None:
        default = api.request("GET", "/api/deviceProfileInfo/default")
        template = api.request("GET", "/api/deviceProfile/" + default["id"]["id"])
        profile = api.request("POST", "/api/deviceProfile", {
            "name": name, "type": "DEFAULT", "transportType": "DEFAULT",
            "provisionType": "DISABLED", "default": False,
            "profileData": template["profileData"],
            "description": "Farm weather demo device profile"})
    if profile["type"] != "DEFAULT" or profile["transportType"] != "DEFAULT":
        raise ValueError("existing device profile incompatible")
    return profile

def ensure_asset(api, name, kind):
    obj = api.named("/api/tenant/assets", name)
    if obj is None:
        obj = api.request("POST", "/api/asset", {"name": name, "type": kind})
    if obj["type"] != kind:
        raise ValueError("existing asset has incompatible type")
    return obj

def ensure_device(api, name, profile, gateway=False):
    obj = api.named("/api/tenant/devices", name)
    if obj is None:
        obj = api.request("POST", "/api/device", {"name": name,
            "deviceProfileId": profile["id"], "additionalInfo": {"gateway": gateway}})
    if obj["deviceProfileId"]["id"] != profile["id"]["id"] or bool((obj.get("additionalInfo") or {}).get("gateway")) != gateway:
        raise ValueError("existing device conflicts with farm topology")
    return obj

def ensure_relation(api, source, target, kind):
    existing = api.request("GET", "/api/relations", fromId=source["id"]["id"],
                           fromType=source["id"]["entityType"])
    if not any(x["to"] == target["id"] and x["type"] == kind for x in existing):
        api.request("POST", "/api/relation", {"from": source["id"], "to": target["id"],
                    "type": kind, "typeGroup": "COMMON"})

def ensure_attributes(api, device, scope, defaults):
    path = "/api/plugins/telemetry/DEVICE/" + device["id"]["id"]
    attrs = api.request("GET", path + "/values/attributes/" + scope, keys=",".join(defaults))
    existing = {a["key"]: a["value"] for a in attrs}
    missing = {k:v for k,v in defaults.items() if k not in existing}
    if missing:
        api.request("POST", path + "/" + scope, missing)

def provision(api):
    station_profile = ensure_profile(api, "Farm Weather Station")
    gateway_profile = ensure_profile(api, "Farm Edge Gateway")
    farm = ensure_asset(api, FARM, "Farm")
    zone = ensure_asset(api, ZONE, "Cultivation Zone")
    station = ensure_device(api, STATION, station_profile)
    gateway = ensure_device(api, GATEWAY, gateway_profile, True)
    for source, target, kind in [(farm,zone,"Contains"), (zone,station,"Contains"),
                                 (farm,gateway,"Contains"), (gateway,station,"Manages")]:
        ensure_relation(api, source, target, kind)
    ensure_attributes(api, station, "SERVER_SCOPE", {"zone_name": ZONE, "dev_eui": "0000000000000001",
        "expected_period_s": 300})
    ensure_attributes(api, station, "SHARED_SCOPE", {"config_version": 1, "wind_alarm_on": 10,
        "wind_alarm_off": 7, "report_period_s": 300})
    # Applied configuration/firmware attributes only come from device acknowledgement (Task 13).
    credentials = api.request("GET", "/api/device/" + gateway["id"]["id"] + "/credentials")
    if credentials["credentialsType"] != "ACCESS_TOKEN" or not credentials["credentialsId"]:
        raise ValueError("gateway must have its own access token")
    topology = {"devices": {STATION: station["id"]["id"]}, "gateway": gateway["id"]["id"],
                "farm": farm["id"]["id"], "zone": zone["id"]["id"],
                "station_profile": station_profile["id"]["id"], "gateway_profile": gateway_profile["id"]["id"]}
    from rules import install
    topology["rule_chains"] = install(api, topology)
    return topology, credentials["credentialsId"]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--ca-file")
    p.add_argument("--devices-output", type=Path, required=True)
    p.add_argument("--gateway-token-output", type=Path, required=True)
    args = p.parse_args()
    api = Rest(args.url, args.ca_file)
    api.login(os.environ["FARM_TB_ADMIN_USER"], os.environ["FARM_TB_ADMIN_PASSWORD"])
    topology, token = provision(api)
    args.devices_output.write_text(json.dumps(topology["devices"], indent=2) + "\n")
    # Refuse accidental overwrite; never put the token in stdout/evidence.
    fd = os.open(args.gateway_token_output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    print(json.dumps(topology, indent=2))

if __name__ == "__main__":
    main()
