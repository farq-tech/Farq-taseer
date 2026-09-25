"""Production: a frame whose parent never answers the sign-in request must open Taseer's own form
within ~3s of the send tap (the send itself is NOT completed: real suppliers)."""
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page(); page.set_default_timeout(30000)
    page.goto("https://taseer.farq.sa/?embed=1", wait_until="domcontentloaded"); page.wait_for_selector("#composer-query")
    page.fill("#composer-query", "أبي سباك يصلح تسريب"); page.locator("button[form=composer]").click()
    page.wait_for_selector(".fq-needcard")
    if page.locator("[data-action=need-city]").count(): page.locator("button[data-action=need-city]:has-text('الرياض')").first.click()
    page.locator("button[data-action=run-search]").click(); page.wait_for_selector(".fq-result", timeout=60000)
    page.locator("button[data-action=toggle]").first.click(); page.locator("button[data-action=review]").click(); page.wait_for_selector("button[data-action=send]")
    t = time.time(); page.locator("button[data-action=send]").click()
    page.wait_for_selector("#auth-form", timeout=10000)
    print("form appeared", round(time.time() - t, 1), "s after the send tap | title:", page.locator("h1").last.inner_text(), "| lead:", page.locator(".fq-lead").first.inner_text()[:90])
    page.screenshot(path="/Users/m4pro/Farq-taseer/docs/audits/qa_2026-09-26/shots/prod_fallback_form.png")
    b.close(); print("DONE")
