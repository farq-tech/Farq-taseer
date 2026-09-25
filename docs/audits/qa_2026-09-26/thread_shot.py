import json, time, urllib.parse
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    ctx.add_cookies([{"name": "farq-auth.2.c0", "value": urllib.parse.quote(json.dumps({"access_token": "farq-good"})), "domain": "127.0.0.1", "path": "/"}])
    page = ctx.new_page(); page.set_default_timeout(20000)
    page.goto("http://127.0.0.1:8790/requests?embed=1", wait_until="domcontentloaded")
    page.wait_for_selector("[data-action=thread]"); page.locator("[data-action=thread]").first.click()
    page.wait_for_selector("#user-reply"); time.sleep(0.8)
    ta = page.locator("#user-reply textarea").bounding_box(); comp = page.locator("#user-reply").bounding_box()
    print("textarea width:", round(ta["width"]), "of composer", round(comp["width"]), "| composer bottom:", round(comp["y"] + comp["height"]), "| nav present:", page.locator(".fq-nav").count())
    page.screenshot(path="/Users/m4pro/Farq-taseer/docs/audits/qa_2026-09-26/shots/local_thread_composer_after.png")
    b.close()
