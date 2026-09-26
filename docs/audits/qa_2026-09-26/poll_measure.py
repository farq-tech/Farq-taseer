"""Calls per minute from an open, idle conversation (production, signed in as the QA account)."""
import json, time
from playwright.sync_api import sync_playwright
acct = json.load(open("/tmp/taseer-local/qa2.json"))
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page(); page.set_default_timeout(30000)
    page.goto("https://taseer.farq.sa/", wait_until="domcontentloaded")
    page.wait_for_selector("[data-action=legacy-auth]"); page.locator("[data-action=legacy-auth]").click()
    page.fill("#auth-email", acct["email"]); page.fill("#auth-password", acct["taseer_password"]); page.locator("button[form=auth-form]").click()
    page.wait_for_selector("[data-action=requests]", timeout=30000); page.locator("[data-action=requests]").first.click()
    page.wait_for_selector("[data-action=thread]"); page.locator("[data-action=thread]").first.click(force=True); page.wait_for_selector("#user-reply")
    calls = []; page.on("request", lambda r: calls.append(time.time()) if "/v1/requests/" in r.url and r.method == "GET" else None)
    t0 = time.time(); time.sleep(130); first = len(calls)
    time.sleep(62); second = len(calls) - first
    print("calls in first 130s:", first, "| calls in the next 62s (quiet phase):", second, "| per minute quiet:", round(second / 62 * 60, 1))
    b.close()
