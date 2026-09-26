import json, time, urllib.parse, urllib.request, sys
from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:8791"; TAG = sys.argv[1] if len(sys.argv) > 1 else "before"
tok = json.load(urllib.request.urlopen(urllib.request.Request(BASE + "/v1/auth/farq", data=json.dumps({"access_token": "farq-good"}).encode(), headers={"Content-Type": "application/json"})))["token"]
reqs = json.load(urllib.request.urlopen(urllib.request.Request(BASE + "/v1/requests", headers={"Authorization": f"Bearer {tok}"})))["requests"]
rid = ([r for r in reqs if not r.get("awarded_seller_id")] or reqs)[0]["id"]
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    ctx.add_cookies([{"name": "farq-auth.2.c0", "value": urllib.parse.quote(json.dumps({"access_token": "farq-good"})), "domain": "127.0.0.1", "path": "/"}])
    page = ctx.new_page(); page.set_default_timeout(20000)
    page.goto(f"{BASE}/r/{rid}?embed=1", wait_until="domcontentloaded"); time.sleep(2.5); print("screen:", page.locator("main").inner_text()[:100].replace(chr(10), " | ")); page.wait_for_selector("#user-reply", timeout=30000); time.sleep(1)
    text = f"اختبار السرعة {int(time.time())}"
    page.fill("#user-reply textarea", text); t0 = time.time(); page.locator("#user-reply .fq-send").click()
    while time.time() - t0 < 10:
        if text in page.locator("#chat-wall").inner_text(): break
        time.sleep(0.02)
    print(TAG, "tap -> bubble on screen:", round((time.time() - t0) * 1000), "ms")
    time.sleep(2); print(TAG, "bubble still there after the server answered:", text in page.locator("#chat-wall").inner_text(), "| duplicates:", page.locator("#chat-wall").inner_text().count(text))
    b.close()
