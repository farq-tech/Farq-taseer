import json, time, urllib.parse, urllib.request
from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:8790"
def api(path, token):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {token}"}); return json.load(urllib.request.urlopen(req, timeout=30))
tok = json.load(urllib.request.urlopen(urllib.request.Request(BASE + "/v1/auth/farq", data=json.dumps({"access_token": "farq-good"}).encode(), headers={"Content-Type": "application/json"})))["token"]
rid = api("/v1/requests", tok)["requests"][0]["id"]
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    ctx.add_cookies([{"name": "farq-auth.2.c0", "value": urllib.parse.quote(json.dumps({"access_token": "farq-good"})), "domain": "127.0.0.1", "path": "/"}])
    page = ctx.new_page(); page.set_default_timeout(30000)
    calls = []; page.on("request", lambda r: calls.append(time.time()) if f"/v1/requests/{rid}" in r.url and r.method == "GET" else None)
    page.goto(f"{BASE}/r/{rid}?embed=1", wait_until="domcontentloaded"); page.wait_for_selector("#user-reply")
    t0 = time.time(); time.sleep(130); first = len(calls); time.sleep(62); second = len(calls) - first
    print("local idle conversation: calls in first 130s:", first, "(", round(first / 130 * 60, 1), "/min ) | next 62s:", second, "(", round(second / 62 * 60, 1), "/min )")
    b.close()
