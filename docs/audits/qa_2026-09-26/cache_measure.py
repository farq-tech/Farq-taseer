"""Production: first-result time on a repeated search (served from the store) and on a fresh sentence
whose search was warmed while M02 was on screen."""
import time, random
from playwright.sync_api import sync_playwright
def journey(b, q, wait_on_m02):
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA"); page = ctx.new_page(); page.set_default_timeout(30000)
    page.goto("https://taseer.farq.sa/?embed=1", wait_until="domcontentloaded"); page.wait_for_selector("#composer-query")
    page.fill("#composer-query", q); page.locator("button[form=composer]").click(); page.wait_for_selector(".fq-needcard")
    if page.locator("[data-action=need-city]").count(): page.locator("button[data-action=need-city]:has-text('الرياض')").first.click()
    time.sleep(wait_on_m02)
    t = time.time(); page.locator("button[data-action=run-search]").click(); page.wait_for_selector(".fq-result", timeout=60000); first = round(time.time() - t, 2)
    for _ in range(200):
        if "البحث مستمر" not in page.locator(".fq-body").inner_text(): break
        time.sleep(0.2)
    out = (first, round(time.time() - t, 2), page.locator(".fq-result").count()); ctx.close(); return out
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    salt = random.randint(100, 999)
    fresh = f"أبي فني تركيب مكيفات في حي النرجس {salt}"
    print("1. fresh sentence, no wait on M02 (warm just started):", journey(b, fresh, 0.2))
    print("2. same sentence again (served from the store):", journey(b, fresh, 0.2))
    fresh2 = f"أبي معلم بلاط للمطبخ حي الياسمين {salt}"
    print("3. fresh sentence, 6s reading M02 (warm had time):", journey(b, fresh2, 6.0))
    b.close(); print("DONE")
