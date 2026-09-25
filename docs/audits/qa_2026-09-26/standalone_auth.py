import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width":390,"height":844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page(); errs=[]; page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto("https://taseer.farq.sa/", wait_until="domcontentloaded"); time.sleep(2)
    print("h1:", page.locator("h1").first.inner_text()); a = page.locator("[data-action=farq-sign-in]"); print("farq button:", a.count(), a.first.get_attribute("href") if a.count() else None, "box:", a.first.bounding_box() if a.count() else None)
    page.screenshot(path="/Users/m4pro/Farq-taseer/docs/audits/qa_2026-09-26/shots/prod_standalone_auth.png")
    print("errors:", errs)
    b.close()
