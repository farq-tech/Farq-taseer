"""Before/after numbers on production for the journey changes: CTA reach in the frame, taps to
results, M02 latency, first-result latency (cold and warm reading)."""
import json, sys, time, urllib.request
from playwright.sync_api import sync_playwright
def note(*a): print(" ".join(str(x) for x in a), flush=True)
def post(path, q):
    req = urllib.request.Request("https://taseer.farq.sa" + path, data=json.dumps({"query": q}).encode(), headers={"Content-Type": "application/json"})
    t = time.time(); r = urllib.request.urlopen(req, timeout=60); r.read(); return round(time.time() - t, 2)
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page(); page.set_default_timeout(30000)
    # 1. CTA reach inside Farq's frame
    page.goto("https://www.farq.sa/taseer", wait_until="domcontentloaded"); fl = page.frame_locator("iframe[data-testid=taseer-embed]")
    fl.locator("button[form=composer]").wait_for(); time.sleep(1)
    ib = page.locator("iframe[data-testid=taseer-embed]").bounding_box(); cb = fl.locator("button[form=composer]").bounding_box()
    note("1. CTA bottom", round(cb["y"] + cb["height"]), "| viewport 844 | inside frame:", cb["y"] + cb["height"] <= ib["y"] + ib["height"] + 1, "| visible without scroll:", cb["y"] + cb["height"] <= 844)
    page.screenshot(path="/Users/m4pro/Farq-taseer/docs/audits/qa_2026-09-26/shots/after_cta_390.png")
    # 2. taps from CTA to results, first visit (no remembered city) then second visit
    for visit in (1, 2):
        c2 = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA") if visit == 1 else ctx2
        pg = c2.new_page(); pg.set_default_timeout(30000); pg.goto("https://taseer.farq.sa/?embed=1", wait_until="domcontentloaded"); pg.wait_for_selector("#composer-query")
        q = "أبي تلفزيون سامسونج 65 بوصة" if visit == 1 else "أبي سباك يصلح تسريب"
        pg.fill("#composer-query", q); taps = 0
        t0 = time.time(); pg.locator("button[form=composer]").click(); taps += 1
        pg.wait_for_selector(".fq-needcard"); t_m02 = round(time.time() - t0, 2)
        if pg.locator("[data-action=need-city]").count(): pg.locator("button[data-action=need-city]:has-text('الرياض')").first.click(); taps += 1
        t1 = time.time(); pg.locator("button[data-action=run-search]").click(); taps += 1
        pg.wait_for_selector(".fq-result", timeout=60000); t_first = round(time.time() - t1, 2)
        note(f"2. visit {visit}: taps CTA→results = {taps} | M02 shown after {t_m02}s | first result {t_first}s after search tap | city asked inline: {visit == 1}")
        if visit == 1: ctx2 = c2
        pg.close()
    b.close()
# 3. API latency: cold vs warm reading (new sentence, then the same sentence again)
q = "أبي نجار يفصل لي مكتبة خشب في الرياض"
note("3. /v1/intent cold:", post("/v1/intent", q), "s | /v1/search warm (reading shared):", post("/v1/search", q), "s | /v1/intent warm:", post("/v1/intent", q), "s")
note("DONE")
