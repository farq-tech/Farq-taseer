import json, time
from playwright.sync_api import sync_playwright
acct = json.load(open("/tmp/taseer-local/qa_account.json"))
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page(); page.set_default_timeout(20000)
    def on_req(r):
        if "auth/v1/token" in r.url or "auth/v1" in r.url:
            body = r.post_data or ""
            print("REQ", r.method, r.url[:90], "| body keys:", list(json.loads(body).keys()) if body.startswith("{") else body[:60], "| email:", (json.loads(body).get("email") if body.startswith("{") else None), flush=True)
    def on_resp(r):
        if "auth/v1" in r.url:
            try: print("RESP", r.status, r.url[:90], r.text()[:200].replace("\n"," "), flush=True)
            except Exception as e: print("RESP", r.status, r.url[:90], e, flush=True)
    page.on("request", on_req); page.on("response", on_resp)
    page.on("console", lambda m: print("CONSOLE", m.type, m.text[:200], flush=True) if m.type in ("error","warning","log") else None)
    page.on("pageerror", lambda e: print("PAGEERROR", str(e)[:200], flush=True))
    seen = []
    page.on("request", lambda r: seen.append(r.url))
    page.goto("https://www.farq.sa/taseer?signin=1", wait_until="domcontentloaded")
    page.wait_for_selector("#auth-signin-email", timeout=30000)
    page.fill("#auth-signin-email", acct["email"]); page.fill("#auth-signin-password", acct["password"])
    print("values:", page.input_value("#auth-signin-email"), len(page.input_value("#auth-signin-password")), flush=True)
    n0 = len(seen); page.locator("[role=dialog] button[type=submit]").first.click(); time.sleep(6)
    print("requests after click:", [u[:100] for u in seen[n0:]][:15], flush=True)
    print("dialog now:", page.locator("[role=dialog]").last.inner_text()[:200].replace("\n"," | "), flush=True)
    print("email inputs:", page.locator("input[type=email]").count(), "| still modal:", page.locator("#auth-signin-email").count(), flush=True)
    b.close()
