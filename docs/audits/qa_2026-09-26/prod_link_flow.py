"""Production: a Farq account meets an old Taseer password account with the same email.
Expected: the frame shows the one-time link screen, the password links them, the nav appears."""
import json, time
from playwright.sync_api import sync_playwright
OUT = "/Users/m4pro/Farq-taseer/docs/audits/qa_2026-09-26/shots"
a = json.load(open("/tmp/taseer-local/qa2.json"))
def note(*x): print(" ".join(str(i) for i in x), flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page(); page.set_default_timeout(30000)
    page.goto("https://www.farq.sa/taseer?signin=1", wait_until="domcontentloaded")
    page.wait_for_selector("#auth-signin-email"); page.fill("#auth-signin-email", a["email"]); page.fill("#auth-signin-password", a["farq_password"])
    page.locator("[role=dialog] button[type=submit]").first.click()
    for _ in range(60):
        if not page.locator("#auth-signin-email").count(): break
        time.sleep(0.5)
    note("A. signed in to Farq as", a["email"])
    ck = page.locator("button:has-text('قبول')"); ck.first.click() if ck.count() else None
    fl = page.frame_locator("iframe[data-testid=taseer-embed]")
    fl.locator("#auth-form").wait_for(timeout=30000); time.sleep(0.5)
    note("B. frame shows the link screen:", fl.locator("h1").last.inner_text(), "|", fl.locator(".fq-lead").first.inner_text()[:80])
    page.screenshot(path=f"{OUT}/prod_link_01_screen.png")
    fl.locator("#auth-email").fill(a["email"]); fl.locator("#auth-password").fill(a["taseer_password"])
    fl.locator("button[form=auth-form]").click()
    fl.locator(".fq-tab").nth(2).wait_for(timeout=30000); time.sleep(1.0)
    note("C. after the password: tabs =", fl.locator(".fq-tab").count(), "| toast/notice:", fl.locator("main").inner_text()[:80].replace("\n"," | "))
    page.screenshot(path=f"{OUT}/prod_link_02_linked.png")
    fl.locator("[data-action=account]").first.click(); time.sleep(1.2)
    note("D. account:", [l for l in fl.locator("main").inner_text().split("\n") if "@" in l or "قديم" in l][:3])
    # a fresh visit: Farq session alone must now be enough
    page.goto("https://www.farq.sa/taseer", wait_until="domcontentloaded"); fl = page.frame_locator("iframe[data-testid=taseer-embed]")
    fl.locator(".fq-tab").nth(2).wait_for(timeout=30000); note("E. fresh visit with Farq session only: tabs =", fl.locator(".fq-tab").count(), "| link screen shown:", fl.locator("#auth-form").count())
    b.close(); note("DONE")
