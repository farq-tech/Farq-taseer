import json, time, urllib.parse, urllib.request, sqlite3
from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:8790"
def api(path, method="GET", body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read() or b"{}")
tok = api("/v1/auth/farq", "POST", {"access_token": "farq-good"})["token"]
reqs = api("/v1/requests", token=tok)["requests"]; rid = ([r for r in reqs if not r.get("awarded_seller_id")] or reqs)[0]["id"]
rec = api(f"/v1/requests/{rid}", token=tok)
seller_msgs = [m for m in rec["messages"] if m["sender_role"] == "seller"]
db = sqlite3.connect("/tmp/taseer-local/farq.sqlite3"); db.row_factory = sqlite3.Row
tokens = [r["reply_token"] for r in db.execute("select reply_token from request_recipients where request_id = ?", (rid,))]
if not seller_msgs:
    api(f"/v1/seller/{tokens[0]}/messages", "POST", {"body": "أقدر أجيك اليوم، السعر 350 ريال شامل", "offer_amount": 350, "delivery_included": True})
    rec = api(f"/v1/requests/{rid}", token=tok); seller_msgs = [m for m in rec["messages"] if m["sender_role"] == "seller"]
api(f"/v1/requests/{rid}/messages", "POST", {"body": "تمام، تقدر تجي بكرة الصباح؟ وهل السعر شامل القطع؟", "reply_to": seller_msgs[0]["id"], "seller_id": seller_msgs[0]["seller_id"]}, token=tok)
api(f"/v1/seller/{tokens[0]}/messages", "POST", {"body": "إيه شامل القطع البسيطة، وأجيك الساعة ٩ الصباح إن شاء الله"})
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    ctx.add_cookies([{"name": "farq-auth.2.c0", "value": urllib.parse.quote(json.dumps({"access_token": "farq-good"})), "domain": "127.0.0.1", "path": "/"}])
    page = ctx.new_page(); page.set_default_timeout(20000)
    page.goto(f"{BASE}/r/{rid}?embed=1", wait_until="domcontentloaded"); time.sleep(2.5)
    print("h1/embar:", page.locator(".fq-embar-title").count(), page.locator(".fq-embar-title").first.inner_text() if page.locator(".fq-embar-title").count() else page.locator("main").inner_text()[:120].replace("\n"," | "))
    page.wait_for_selector("#user-reply", timeout=30000); time.sleep(0.5)
    print("quotes on screen:", page.locator(".fq-quote").count(), "| tails:", page.locator(".tail").count(), "| jump hidden:", page.locator(".fq-tobottom").get_attribute("hidden") is not None)
    page.screenshot(path="/Users/m4pro/Farq-taseer/docs/audits/qa_2026-09-26/shots/local_thread_reply.png")
    b.close()
