import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width":390,"height":844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page()
    page.goto("https://www.farq.sa/taseer", wait_until="domcontentloaded")
    for i in range(12):
        time.sleep(1)
        print(i, "frames:", [(f.name, f.url[:80]) for f in page.frames], flush=True)
        el = page.locator("iframe")
        print("   iframe elements:", el.count(), [el.nth(j).get_attribute("src") for j in range(el.count())], flush=True)
        if len(page.frames) > 1: break
    fr = page.frames[-1]
    try:
        print("child frame content h1:", fr.locator("h1").first.inner_text(timeout=5000))
        print("composer count:", fr.locator("#composer-query").count())
    except Exception as e: print("child frame err", e)
    ib = page.locator("iframe").first.bounding_box(); print("iframe box", ib)
    print("header height (top of iframe):", ib and ib["y"])
    b.close()
