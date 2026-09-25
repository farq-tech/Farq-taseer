"""Smoke: the other Farq sectors still render after the /taseer change (shared shell untouched, but verify)."""
import time
from playwright.sync_api import sync_playwright
def run(view, tag):
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport=view, device_scale_factor=2, is_mobile=view["width"] < 800, has_touch=view["width"] < 800, locale="ar-SA")
        page = ctx.new_page(); errs = []; page.on("pageerror", lambda e: errs.append(str(e)))
        fails = []; page.on("response", lambda r: fails.append(f"{r.status} {r.url[:90]}") if r.status >= 500 else None)
        for path, must in (("/?scope=restaurant&vertical=restaurant", "مطعم"), ("/?scope=grocery&vertical=grocery", "بقالة"), ("/taseer", "تسعير")):
            page.goto("https://www.farq.sa" + path, wait_until="domcontentloaded"); time.sleep(3)
            txt = page.locator("body").inner_text()
            tabs = page.locator(".sector-tab"); labels = [tabs.nth(i).inner_text().replace("\n", " ") for i in range(tabs.count())]
            print(tag, path, "| ok:", must in txt, "| tabs:", labels, "| h1:", page.locator("h1").first.inner_text()[:40] if page.locator("h1").count() else None, "| horizontal overflow:", page.evaluate("document.documentElement.scrollWidth > window.innerWidth"), flush=True)
            page.screenshot(path=f"/Users/m4pro/Farq-taseer/docs/audits/qa_2026-09-26/shots/smoke_{tag}_{must}.png")
        print(tag, "js errors:", errs[:5], "| 5xx:", fails[:5], flush=True)
        b.close()
run({"width": 390, "height": 844}, "m390"); run({"width": 430, "height": 932}, "m430"); run({"width": 1440, "height": 900}, "d1440")
print("DONE")
