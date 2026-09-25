"""Local end-to-end of the unified account: Farq cookie -> auto sign-in -> send -> reply -> compare -> award.
Haraj sending is NOT configured locally (NotConnectedChat), so no real supplier is contacted."""
import json, os, sqlite3, time, urllib.parse, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8790"
OUT = os.path.dirname(os.path.abspath(__file__)) + "/shots"; os.makedirs(OUT, exist_ok=True)
def note(*a): print(" ".join(str(x) for x in a), flush=True)
def shot(page, n): page.screenshot(path=f"{OUT}/local_{n}.png")
def api(path, method="GET", body=None, token=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items(): req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    ctx.add_cookies([{"name": "farq-auth.2.c0", "value": urllib.parse.quote(json.dumps({"access_token": "farq-good", "user": {"email": "sara@farq-qa.test"}})), "domain": "127.0.0.1", "path": "/"}])
    page = ctx.new_page(); page.set_default_timeout(20000)
    errors = []; page.on("pageerror", lambda e: errors.append(str(e))); page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(BASE + "/?embed=1", wait_until="domcontentloaded")
    page.wait_for_selector("#composer-query")
    page.wait_for_function("document.querySelectorAll('.fq-tab').length === 3", timeout=10000)
    note("A. auto sign-in from Farq cookie: nav tabs =", page.locator(".fq-tab").count(), "| token in storage:", bool(page.evaluate("localStorage.getItem('farq.token')")))
    shot(page, "01_home_signed_in")
    page.fill("#composer-query", "أبي سباك يصلح تسريب وكهربائي يركب 3 أفياش")
    page.locator("button[form=composer]").scroll_into_view_if_needed(); page.locator("button[form=composer]").click()
    page.wait_for_selector("button[data-action=search-city]"); page.locator("button[data-action=search-city]:has-text('الرياض')").first.click()
    page.wait_for_selector(".fq-needcard"); note("B. understand cards:", page.locator(".fq-needcard").count())
    page.locator("button[data-action=run-search]").click()
    t = time.time(); page.wait_for_selector(".fq-result", timeout=60000); note("C. first result after", round(time.time()-t, 1), "s")
    for _ in range(150):
        if "البحث مستمر" not in page.locator(".fq-body").inner_text(): break
        time.sleep(0.3)
    n = page.locator(".fq-result").count(); note("   results:", n)
    page.locator("button[data-action=toggle]").nth(0).click(); page.locator("button[data-action=toggle]").nth(1).click()
    page.locator("button[data-action=review]").click(); page.wait_for_selector("button[data-action=send]")
    shot(page, "02_review")
    # double tap send
    send = page.locator("button[data-action=send]"); send.click(); 
    try: send.click(timeout=300)
    except Exception: pass
    page.wait_for_function("document.body.innerText.includes('تم إرسال طلبك') || document.body.innerText.includes('ما قدرنا نرسل')", timeout=30000)
    shot(page, "03a_after_send")
    note("D. sent screen reached; sellers line:", page.locator(".fq-facts").inner_text().replace("\n", " | "))
    shot(page, "03_sent")
    token = page.evaluate("localStorage.getItem('farq.token')")
    st, lst = api("/v1/requests", token=token); note("   requests on server after double tap:", len(lst.get("requests", [])))
    rid = lst["requests"][0]["id"]
    # idempotency replay through the API
    body = {"original_text": "أبي نجار", "need": "نجار", "city": "الرياض", "recipients": [{"seller_id": "14371810", "seller_name": "نجار"}]}
    s1, r1 = api("/v1/requests", "POST", body, token, {"Idempotency-Key": "k-1"}); s2, r2 = api("/v1/requests", "POST", body, token, {"Idempotency-Key": "k-1"})
    note("E. idempotent replay:", s1, s2, "same id:", r1.get("id") == r2.get("id"))
    st, lst = api("/v1/requests", token=token); note("   total requests now:", len(lst["requests"]))
    # supplier replies with prices
    db = sqlite3.connect("/tmp/taseer-local/farq.sqlite3"); db.row_factory = sqlite3.Row
    recips = db.execute("select seller_id, seller_name, reply_token, need from request_recipients where request_id = ?", (rid,)).fetchall()
    note("F. recipients:", [(r["seller_name"][:20], r["need"]) for r in recips])
    s, v = api(f"/v1/seller/{recips[0]['reply_token']}"); note("   seller view keys:", sorted(v.keys())[:8], "| other seller prices visible:", "price" in json.dumps(v, ensure_ascii=False))
    s, _ = api(f"/v1/seller/{recips[0]['reply_token']}/messages", "POST", {"body": "أقدر أجيك اليوم، السعر 350 ريال شامل", "offer_amount": 350, "delivery_included": True, "phone": "0555555555"}); note("   reply 1:", s)
    s, _ = api(f"/v1/seller/{recips[1]['reply_token']}/messages", "POST", {"body": "300 ريال بدون توصيل", "offer_amount": 300, "delivery_included": False, "delivery_price": 80}); note("   reply 2:", s)
    s, v2 = api(f"/v1/seller/{recips[1]['reply_token']}"); leak = "350" in json.dumps(v2, ensure_ascii=False); note("   seller 2 sees seller 1 price (350)?", leak)
    # customer opens the conversation
    if page.locator("[data-action=dismiss-notify]").count():
        note("   push prompt shown over the success screen; dismissing"); page.locator("button[data-action=dismiss-notify]").first.click(); time.sleep(0.3)
    page.locator("button[data-action=open-sent]").click(); page.wait_for_selector("[data-action=thread]")
    shot(page, "04_requests")
    page.locator("[data-action=thread]").first.click(); page.wait_for_selector(".fq-composer, #user-reply", timeout=15000); time.sleep(0.8)
    body_text = page.locator("main").inner_text(); note("G. thread shows 350:", "350" in body_text, "| 300:", "300" in body_text, "| cheapest badge:", "الأرخص" in body_text)
    shot(page, "05_thread")
    if page.locator("[data-action=open-compare]").count():
        page.locator("[data-action=open-compare]").first.click(); time.sleep(0.6); shot(page, "06_compare")
        note("H. compare text:", page.locator("main").inner_text().replace("\n", " | ")[:400])
        if page.locator("[data-action=pick-winner]").count():
            page.locator("[data-action=pick-winner]").first.click(); time.sleep(0.4); shot(page, "07_award_sheet")
            if page.locator("[data-action=confirm-award]").count():
                page.locator("[data-action=confirm-award]").click(); time.sleep(1.0); shot(page, "08_awarded")
                note("I. after award:", page.locator("main").inner_text().replace("\n", " | ")[:400])
    st, rec = api(f"/v1/requests/{rid}", token=token); note("J. request status:", rec.get("status"), "awarded:", rec.get("awarded_seller_id"))
    note("--- js errors:", errors[:10])
    b.close(); note("DONE")
