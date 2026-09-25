import json, time
from playwright.sync_api import sync_playwright
acct = json.load(open("/tmp/taseer-local/qa_account.json"))
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page(); page.set_default_timeout(30000)
    calls = []
    page.on("request", lambda r: calls.append((round(time.time(), 1), r.method, r.url[:90])) if "taseer.farq.sa/v1" in r.url or "api/auth" in r.url else None)
    page.goto("https://www.farq.sa/taseer?signin=1", wait_until="domcontentloaded")
    page.wait_for_selector("#auth-signin-email"); page.fill("#auth-signin-email", acct["email"]); page.fill("#auth-signin-password", acct["password"])
    page.locator("[role=dialog] button[type=submit]").first.click()
    fl = page.frame_locator("iframe[data-testid=taseer-embed]")
    fl.locator(".fq-tab").nth(2).wait_for(timeout=30000); t0 = time.time(); n0 = len(calls)
    renders = fl.locator("#app").evaluate("""el => new Promise(res => { let n = 0; const mo = new MutationObserver(m => { n += m.length; }); mo.observe(el, {childList: true, subtree: true}); setTimeout(() => { mo.disconnect(); res(n); }, 6000); })""")
    print("DOM mutations in the frame over 6s after sign-in:", renders)
    print("network calls in that window:", [c for c in calls[n0:]][:30])
    print("view now:", fl.locator("h1").first.inner_text(), "| tabs:", fl.locator(".fq-tab").count())
    # try the account tab with force
    fl.locator("[data-action=account]").first.click(force=True); time.sleep(1.5)
    print("after account tap:", fl.locator("main").inner_text()[:200].replace("\n", " | "))
    page.screenshot(path=f"{D}/shots/prod_s_03_account.png" if False else "/Users/m4pro/Farq-taseer/docs/audits/qa_2026-09-26/shots/prod_s_03_account.png")
    b.close()
