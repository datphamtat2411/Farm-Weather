"""Provision native ThingsBoard farm-scoped RBAC and isolated service credentials.

Run as the tenant administrator against the existing Task 10/11 topology.
Never change unrelated roles, users or resource ownership.
"""
import argparse
import json
import os
import time
import urllib.parse
from pathlib import Path

from provision import Rest, ApiError, provision, STATION

CUSTOMER = "Farm Weather Operations"
HUMAN_ROLES = ("admin", "technician", "operator", "viewer")
ROLES = HUMAN_ROLES + ("reader", "control")
READ = ["READ", "READ_ATTRIBUTES", "READ_TELEMETRY"]
ADMIN_GENERIC = {
    "USER": ["READ", "CREATE", "WRITE", "DELETE"],
    "USER_GROUP": ["READ", "CREATE", "WRITE", "DELETE", "ADD_TO_GROUP", "REMOVE_FROM_GROUP"],
    "ROLE": ["READ", "CREATE", "WRITE", "DELETE"],
    "GROUP_PERMISSION": ["READ", "CREATE", "WRITE", "DELETE"],
}
VIEW_GENERIC = {"ALARM": ["READ"], "WIDGET_TYPE": ["READ"], "WIDGETS_BUNDLE": ["READ"],
                "PROFILE": ["WRITE"], "CUSTOMER": ["READ"]}
SERVICE_GENERIC = {"ALARM": ["READ", "WRITE"], "CUSTOMER": ["READ"]}
NULL_UUID = "13814000-1dd2-11b2-8080-808080808080"

def uid(obj):
    return obj["id"]["id"]

def identifier(obj):
    return obj["id"] if isinstance(obj["id"], str) else uid(obj)

def page_items(api, path, **query):
    page = 0
    while True:
        data = api.request("GET", path, pageSize=100, page=page, **query)
        yield from data["data"]
        if not data["hasNext"]:
            return
        page += 1

def unique(items, field, value):
    matches = [x for x in items if x.get(field) == value]
    if len(matches) > 1:
        raise ValueError("ambiguous security object: " + value)
    return matches[0] if matches else None

def ensure_customer(api):
    customer = unique(page_items(api, "/api/customers", textSearch=CUSTOMER), "title", CUSTOMER)
    return customer or api.request("POST", "/api/customer", {"title": CUSTOMER})

def group(api, customer, kind, name):
    path = f"/api/entityGroups/CUSTOMER/{uid(customer)}/{kind}"
    existing = unique(api.request("GET", path), "name", name)
    return existing or api.request("POST", "/api/entityGroup",
        {"name": name, "type": kind, "ownerId": customer["id"]})

def members(api, group_id):
    return {identifier(x) for x in page_items(api, f"/api/entityGroup/{group_id}/entities")}

def assign(api, customer, typ, ident, target):
    obj = api.request("GET", f"/api/{typ.lower()}/{ident}")
    owner = (obj.get("customerId") or {}).get("id")
    if owner not in (None, NULL_UUID, "00000000-0000-0000-0000-000000000000", uid(customer)):
        raise ValueError("refusing to move another customer's resource")
    if owner != uid(customer):
        api.request("POST", f"/api/owner/CUSTOMER/{uid(customer)}/{typ}/{ident}", [uid(target)])
    elif ident not in members(api, uid(target)):
        api.request("POST", f"/api/entityGroup/{uid(target)}/addEntities", [ident])

def role(api, customer, name, kind, permissions):
    existing = unique(
        (x for x in page_items(api, "/api/roles", textSearch=name)
         if (x.get("customerId") or {}).get("id") == uid(customer)), "name", name)
    if existing is None:
        return api.request("POST", "/api/role",
            {"name": name, "type": kind, "permissions": permissions, "customerId": customer["id"]})
    def canonical(p):
        return sorted(p) if isinstance(p, list) else {k: sorted(v) for k, v in p.items()}
    if existing["type"] != kind or canonical(existing["permissions"]) != canonical(permissions) or existing.get("excludedPermissions"):
        raise ValueError("security role drift; review before reprovisioning: " + name)
    return existing

def bind(api, user_group, role_obj, resource_group=None):
    path = f"/api/userGroup/{uid(user_group)}/groupPermissions"
    target = uid(resource_group) if resource_group else None
    perms = api.request("GET", path)
    match = [p for p in perms if p["roleId"]["id"] == uid(role_obj)
             and (p.get("entityGroupId") or {}).get("id") == target]
    if len(match) > 1:
        raise ValueError("duplicate role binding")
    if match:
        return uid(match[0])
    data = {"userGroupId": user_group["id"], "roleId": role_obj["id"]}
    if resource_group:
        data["entityGroupId"] = resource_group["id"]
    return uid(api.request("POST", "/api/groupPermission", data))

def ensure_user(api, customer, user_group, email):
    path = f"/api/customer/{uid(customer)}/users"
    obj = unique(page_items(api, path, textSearch=email), "email", email)
    created = obj is None
    if created:
        obj = api.request("POST", "/api/user", {"email": email, "authority": "CUSTOMER_USER",
            "customerId": customer["id"], "firstName": "Farm", "lastName": user_group["name"]},
            sendActivationMail="false", entityGroupId=uid(user_group))
    if obj["authority"] != "CUSTOMER_USER" or obj["customerId"] != customer["id"]:
        raise ValueError("unexpected user owner/authority")
    # All is an inventory group, not a privilege grant. Other memberships are
    # deliberately not silently removed: fail closed on unexpected grants.
    actual = {identifier(x) for x in api.request("GET", f"/api/entityGroups/USER/{uid(obj)}")}
    inventory = [g for g in api.request("GET", f"/api/entityGroups/CUSTOMER/{uid(customer)}/USER")
                 if g["name"] == "All"]
    permitted = {uid(user_group)} | {uid(g) for g in inventory}
    if uid(user_group) not in actual or not actual <= permitted:
        raise ValueError("unexpected user group membership")
    return obj, created

def activate(api, user, password):
    link = api.request("GET", f"/api/user/{uid(user)}/activationLinkInfo")["value"]
    token = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)["activateToken"][0]
    unauth = Rest(api.url, api.ca_file, authorization=None, timeout=api.timeout)
    # Reuse the already verified CA context; no credentials are sent elsewhere.
    unauth.request("POST", "/api/noauth/activate", {"activateToken": token, "password": password},
                   sendActivationMail="false")

def issue_key(api, user, description):
    # Only a new credential is returned in clear text. Never silently create
    # an extra key on every provision; rotation is explicit.
    existing = list(page_items(api, f"/apiKeys/{uid(user)}"))
    if any(k["description"] == description and k["enabled"] for k in existing):
        raise ValueError("API key already exists; use saved secret or explicitly revoke/rotate")
    return api.request("POST", "/apiKey", {"userId": user["id"], "description": description,
        "enabled": True, "expirationTime": int(time.time()*1000) + 30*86400000})

def install_security(api, topology, emails):
    if set(emails) != set(ROLES) or len(set(emails.values())) != len(ROLES):
        raise ValueError("six distinct named identities required")
    customer = ensure_customer(api)
    station_group = group(api, customer, "DEVICE", "Farm managed stations")
    gateway_group = group(api, customer, "DEVICE", "Farm gateway diagnostics")
    asset_group = group(api, customer, "ASSET", "Farm assets")
    dashboard_group = group(api, customer, "DASHBOARD", "Farm dashboards")
    for ident in topology["devices"].values():
        assign(api, customer, "DEVICE", ident, station_group)
    assign(api, customer, "DEVICE", topology["gateway"], gateway_group)
    for key in ("farm", "zone"):
        assign(api, customer, "ASSET", topology[key], asset_group)
    for target, expected in [(station_group, set(topology["devices"].values())),
                             (gateway_group, {topology["gateway"]}),
                             (asset_group, {topology["farm"], topology["zone"]})]:
        if members(api, uid(target)) != expected:
            raise ValueError("resource group contains unexpected entities")
    user_groups, users, created = {}, {}, {}
    for name in ROLES:
        ug = group(api, customer, "USER", "Farm " + name)
        user_groups[name] = uid(ug)
        expected = set()
        operations = ["READ", "READ_TELEMETRY"] if name == "reader" else READ
        if name == "control":
            operations = READ + ["RPC_CALL"]
        device_role = role(api, customer, "Farm " + name + " station", "GROUP", operations)
        expected.add(bind(api, ug, device_role, station_group))
        if name in HUMAN_ROLES:
            read_role = role(api, customer, "Farm resource read", "GROUP", READ)
            for target in (gateway_group, asset_group, dashboard_group):
                expected.add(bind(api, ug, read_role, target))
            perms = dict(VIEW_GENERIC)
            if name == "admin":
                perms.update(ADMIN_GENERIC)
            generic = role(api, customer, "Farm " + name + " general", "GENERIC", perms)
            expected.add(bind(api, ug, generic))
        elif name == "control":
            generic = role(api, customer, "Farm control service general", "GENERIC", SERVICE_GENERIC)
            expected.add(bind(api, ug, generic))
            # Lookup only actual farm human users, not other service identities.
            lookup = role(api, customer, "Farm human group lookup", "GROUP", ["READ"])
            for human in HUMAN_ROLES:
                expected.add(bind(api, ug, lookup, group(api, customer, "USER", "Farm " + human)))
        actual = {uid(p) for p in api.request("GET", f"/api/userGroup/{uid(ug)}/groupPermissions")}
        if actual != expected:
            raise ValueError("unexpected group permission; review existing grants")
        users[name], created[name] = ensure_user(api, customer, ug, emails[name])
    return {"customer": uid(customer), "station_ids": list(topology["devices"].values()),
            "gateway": topology["gateway"], "groups": user_groups,
            "dashboard_group": uid(dashboard_group), "users": {k:uid(v) for k,v in users.items()}}, users, created

def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(data + "\n")

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--ca-file")
    p.add_argument("--output", type=Path, required=True, help="new private directory outside repo")
    args = p.parse_args()
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    api = Rest(args.url, args.ca_file)
    api.login(os.environ["FARM_TB_ADMIN_USER"], os.environ["FARM_TB_ADMIN_PASSWORD"])
    topology, _ = provision(api)
    emails = {name:os.environ["FARM_" + name.upper() + "_EMAIL"] for name in ROLES}
    manifest, users, created = install_security(api, topology, emails)
    for name in ROLES:
        if created[name]:
            activate(api, users[name], os.environ["FARM_" + name.upper() + "_PASSWORD"])
    for name in ("reader", "control"):
        key = issue_key(api, users[name], "Farm " + name + " service")
        write_private(args.output/(name+".secret"), key["value"])
    write_private(args.output/"security.json", json.dumps(manifest, indent=2))
    print("Farm security provisioned; private output contains service credentials")

if __name__ == "__main__":
    main()
