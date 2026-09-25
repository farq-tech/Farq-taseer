"""Layout at the bottom of the Taseer frame on web, and the cost of one supplier tap on the results list."""
import time, sys
from playwright.sync_api import sync_playwright
URL = sys.argv[1] if len(sys.argv) > 1 else "https://taseer.farq.sa/?embed=1"
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page(); page.set_default_timeout(30000)
    # A. bottom geometry inside Farq's frame (consent accepted)
    page.goto("https://www.farq.sa/taseer", wait_until="domcontentloaded"); page.wait_for_selector("iframe[data-testid=taseer-embed]"); time.sleep(2)
    ck = page.locator("button:has-text('قبول')"); ck.first.click() if ck.count() else None; time.sleep(0.5)
    fl = page.frame_locator("iframe[data-testid=taseer-embed]"); ib = page.locator("iframe[data-testid=taseer-embed]").bounding_box(); nb = fl.locator(".fq-nav").bounding_box()
    print("A. web: iframe bottom", round(ib["y"] + ib["height"]), "| nav bottom", round(nb["y"] + nb["height"]), "| gap under nav:", round(844 - (nb["y"] + nb["height"])), "px")
    # B. tap cost on the results list
    page.goto(URL, wait_until="domcontentloaded"); page.wait_for_selector("#composer-query")
    page.fill("#composer-query", "أبي سباك يصلح تسريب وكهربائي يركب 3 أفياش"); page.locator("button[form=composer]").click(); page.wait_for_selector(".fq-needcard")
    if page.locator("[data-action=need-city]").count(): page.locator("button[data-action=need-city]:has-text('الرياض')").first.click()
    page.locator("button[data-action=run-search]").click(); page.wait_for_selector(".fq-result", timeout=60000)
    for _ in range(150):
        if "البحث مستمر" not in page.locator(".fq-body").inner_text(): break
        time.sleep(0.3)
    n = page.locator(".fq-result").count(); print("B. results on screen:", n)
    page.evaluate("window.scrollTo(0, 1200)"); time.sleep(0.3); y0 = page.evaluate("window.scrollY")
    stats = page.evaluate("""() => new Promise(res => {
        const app = document.querySelector('#app'); let muts = 0; const imgs0 = app.querySelectorAll('img').length;
        const mo = new MutationObserver(m => { muts += m.length; }); mo.observe(app, {childList: true, subtree: true, attributes: true});
        const btn = [...document.querySelectorAll('button[data-action=toggle]')].filter(b => b.getBoundingClientRect().top > 0 && b.getBoundingClientRect().top < 800)[0] || document.querySelector('button[data-action=toggle]');
        const t0 = performance.now(); btn.click();
        requestAnimationFrame(() => requestAnimationFrame(() => { const t1 = performance.now(); setTimeout(() => { mo.disconnect(); res({ms: Math.round(t1 - t0), muts, picked: app.querySelectorAll('.fq-result.picked').length, scrollY: window.scrollY, sticky: !!app.querySelector('.fq-sticky')}); }, 200); }));
    })""")
    print("   one tap:", stats, "| scroll before:", y0)
    b.close(); print("DONE")
