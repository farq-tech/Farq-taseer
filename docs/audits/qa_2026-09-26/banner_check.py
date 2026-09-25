import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page(); page.set_default_timeout(30000)
    page.goto("https://www.farq.sa/taseer", wait_until="domcontentloaded"); page.wait_for_selector("iframe[data-testid=taseer-embed]"); time.sleep(3)
    ib = page.locator("iframe[data-testid=taseer-embed]").bounding_box(); bb = page.locator("[role=dialog][data-sector]").first.bounding_box()
    print("iframe bottom:", round(ib["y"]+ib["height"]), "| banner top:", round(bb["y"]) if bb else None, "| clear:", (bb is None) or (ib["y"]+ib["height"] <= bb["y"]+1))
    fl = page.frame_locator("iframe[data-testid=taseer-embed]"); tab = fl.locator(".fq-tab").first; tb = tab.bounding_box()
    print("frame nav tab box:", tb, "| above banner:", tb and bb and tb["y"]+tb["height"] <= bb["y"]+1)
    page.screenshot(path="/Users/m4pro/Farq-taseer/docs/audits/qa_2026-09-26/shots/prod_banner_fixed_390.png")
    b.close()
