"""Real native ThingsBoard widget, scoped HTTPS API, and Chromium browser checks."""
from pathlib import Path
from urllib.parse import urlsplit
import time
from playwright.sync_api import sync_playwright, expect
from dashboard import install, FQN
from verify_rule_engine import eventually
from provision import ApiError

def exercise_dashboard(admin,topology,manifest,clients,emails,passwords,call,adapter,station,url,endpoint,evidence):
    ident=install(admin,topology,manifest)
    assert install(admin,topology,manifest)==ident
    for name in ("admin","technician","operator","viewer"):
        assert clients[name].request("GET","/api/dashboard/"+ident)["id"]["id"]==ident
        assert clients[name].request("GET","/api/widgetType",fqn="tenant."+FQN)["fqn"]==FQN
    stamp=int(time.time()*1000)
    adapter.publish_health({"disk_bytes":1024,"budget_bytes":4096,"pending":3,"oldest_age_ms":60000,
        "state":"RECOVERING","radio_diagnostics":{station:{"rssi":-73,"snr":8,"received_at":stamp,"simulated":True}}},stamp)
    def ready():
        s=call("viewer","overview",{})
        return s if s["gateway"]["pending"]==3 and s["values"]["temperature"]==29.5 and s["freshness"]=="FRESH" else None
    snapshot=eventually(ready,"real dashboard snapshot not available",seconds=40)
    assert snapshot["role"]=="viewer" and snapshot["capabilities"]==[]
    assert snapshot["gateway"]["radio"]["simulated"] and snapshot["gateway"]["radio"]["rssi"]==-73
    assert snapshot["applied"]["applied_config_version"]==3
    assert snapshot["desired"]["config_version"]==3
    assert snapshot["alarms"]
    try:
        call("viewer","overview",{"other":True})
    except ApiError as e:
        assert e.status==400
    else:
        raise AssertionError("unexpected overview parameter admitted")
    screenshots=Path(evidence)
    screenshots.mkdir(exist_ok=True,parents=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        for role,viewport in [("viewer",{"width":390,"height":844}),("operator",{"width":1440,"height":1000})]:
            context=browser.new_context(ignore_https_errors=True,viewport=viewport)
            page=context.new_page()
            # Production nginx routes /api/farm to the loopback HTTPS FarmApi.
            # CI forwards precisely that path to the real service; no synthetic response,
            # JWT injection, fake browser DOM, or mocked ThingsBoard backend.
            def forward(route):
                request=route.request
                assert request.headers.get("x-authorization","").startswith("Bearer ")
                response=route.fetch(url=endpoint+urlsplit(request.url).path)
                route.fulfill(response=response)
            page.route("**/api/farm/stations/**",forward)
            page.goto(url+"/login",wait_until="domcontentloaded")
            page.locator("#username-input").fill(emails[role])
            page.locator("#password-input").fill(passwords[role])
            with page.expect_response(lambda r:r.url.endswith("/api/auth/login") and r.status==200):
                page.locator("form.tb-login-form button[type=submit]").click()
            page.wait_for_url(lambda u:"/login" not in u,timeout=60000)
            page.goto(url+"/dashboards/"+ident,wait_until="domcontentloaded")
            expect(page.locator(".fw-shell")).to_be_visible(timeout=60000)
            expect(page.locator('[data-key="temperature"] strong')).to_have_text("29,5 °C",timeout=60000)
            expect(page.locator("#fw-fresh")).to_have_text("Dữ liệu hiện hành")
            expect(page.locator("#fw-health")).to_contain_text("RECOVERING")
            expect(page.locator("#fw-health")).to_contain_text("mô phỏng")
            expect(page.locator("#fw-config-state")).to_contain_text("3")
            expect(page.locator("#fw-test")).to_be_hidden()
            expect(page.locator("#fw-config")).to_be_hidden()
            if role=="viewer":
                expect(page.locator("#fw-stop")).to_be_hidden()
                expect(page.get_by_role("button",name="Xác nhận đã đọc")).to_have_count(0)
            else:
                expect(page.locator("#fw-stop")).to_be_visible()
                ack=page.get_by_role("button",name="Xác nhận đã đọc")
                # A real active unacknowledged wind alarm is present.
                ack.first.click()
                expect(page.locator("#fw-alarms")).to_contain_text("Đã xác nhận",timeout=30000)
                page.on("dialog",lambda dialog:dialog.accept())
                page.locator("#fw-stop").click()
                expect(page.locator("#fw-command")).to_contain_text("Chờ thiết bị",timeout=30000)
            assert page.locator(".fw-shell").evaluate("(e)=>e.scrollWidth<=e.clientWidth+2")
            page.locator(".fw-shell").screenshot(path=str(screenshots/f"dashboard-{role}.png"))
            context.close()
        browser.close()
    return {"dashboard_id":ident,"browser_roles":["viewer","operator"],"native":True,
            "responsive":True,"verified_gateway":True,"real_rpc_admission":True}
