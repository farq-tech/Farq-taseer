import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page(); page.set_default_timeout(30000)
    page.goto("https://www.farq.sa/taseer", wait_until="domcontentloaded"); ck = page.locator("button:has-text('قبول')")
    fl = page.frame_locator("iframe[data-testid=taseer-embed]"); fl.locator("#composer-query").wait_for(); 
    if ck.count(): ck.first.click()
    fl.locator("#composer-query").fill("أبي تلفزيون سامسونج 65 بوصة"); fl.locator("button[form=composer]").click(); fl.locator(".fq-needcard").first.wait_for()
    bar = fl.locator(".fq-embar"); print("back bar on M02:", bar.count(), "| title:", fl.locator(".fq-embar-title").first.inner_text() if bar.count() else None, "| box:", fl.locator(".fq-embar-back").first.bounding_box() if bar.count() else None)
    page.screenshot(path="/Users/m4pro/Farq-taseer/docs/audits/qa_2026-09-26/shots/prod_backbar_m02.png")
    fl.locator(".fq-embar-back").first.click(); time.sleep(0.6)
    print("after back tap: composer visible:", fl.locator("#composer-query").count() > 0, "| bar gone:", fl.locator(".fq-embar").count() == 0)
    b.close(); print("DONE")
