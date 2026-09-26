"""How much the results screen churns during a streamed search (before/after append-only updates)."""
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    page = ctx.new_page(); page.set_default_timeout(30000)
    page.goto("https://taseer.farq.sa/?embed=1", wait_until="domcontentloaded"); page.wait_for_selector("#composer-query")
    page.fill("#composer-query", "أبي سباك يصلح تسريب وكهربائي يركب 3 أفياش"); page.locator("button[form=composer]").click(); page.wait_for_selector(".fq-needcard")
    if page.locator("[data-action=need-city]").count(): page.locator("button[data-action=need-city]:has-text('الرياض')").first.click()
    page.evaluate("""() => { window.__m = {muts: 0, renders: 0, imgs: 0, long: 0}; const app = document.querySelector('#app');
      new MutationObserver(ms => { window.__m.muts += ms.length; for (const m of ms) if (m.type === 'childList' && [...m.addedNodes].some(n => n.nodeType === 1 && n.matches && n.matches('main.fq'))) window.__m.renders++; }).observe(app, {childList: true, subtree: true, attributes: true});
      new PerformanceObserver(l => { for (const e of l.getEntries()) window.__m.long += e.duration; }).observe({type: 'longtask', buffered: true});
    }""")
    t0 = time.time(); page.locator("button[data-action=run-search]").click(); page.wait_for_selector(".fq-result", timeout=60000)
    for _ in range(200):
        if "البحث مستمر" not in page.locator(".fq-body").inner_text(): break
        time.sleep(0.25)
    m = page.evaluate("window.__m"); print("search took", round(time.time() - t0, 1), "s | results:", page.locator(".fq-result").count(), "| full screen rebuilds:", m["renders"], "| DOM mutations:", m["muts"], "| long-task ms:", round(m["long"]))
    b.close()
