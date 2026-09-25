"""Drive Farq.sa -> Taseer as a first-time Saudi user. Production, read-only:
never signs in to Taseer and never sends a quote request (the send tap must stop at auth)."""
import json, sys, time, os
from playwright.sync_api import sync_playwright

OUT = os.path.dirname(os.path.abspath(__file__)) + "/shots"
os.makedirs(OUT, exist_ok=True)
QUERY = sys.argv[1] if len(sys.argv) > 1 else "أبي سباك يصلح تسريب وكهربائي يركب 3 أفياش"
VIEW = (390, 844)
if len(sys.argv) > 2:
    w, h = sys.argv[2].split("x"); VIEW = (int(w), int(h))
TAG = sys.argv[3] if len(sys.argv) > 3 else "m"
START = sys.argv[4] if len(sys.argv) > 4 else "https://www.farq.sa/"

def note(*a):
    print(" ".join(str(x) for x in a), flush=True)

def shot(page, name):
    page.screenshot(path=f"{OUT}/{TAG}_{name}.png", full_page=False)

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    mobile = VIEW[0] < 800
    kw = dict(viewport={"width": VIEW[0], "height": VIEW[1]}, device_scale_factor=2, is_mobile=mobile, has_touch=mobile, locale="ar-SA")
    if mobile: kw["user_agent"] = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ctx = browser.new_context(**kw)
    page = ctx.new_page()
    page.set_default_timeout(15000)
    console = []; failed = []; reqs = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: console.append(f"[pageerror] {e}"))
    page.on("requestfailed", lambda r: failed.append(f"{r.method} {r.url} :: {r.failure}"))
    def on_resp(r):
        try:
            if r.status >= 400: failed.append(f"HTTP {r.status} {r.request.method} {r.url}")
            u = r.url
            if "/v1/" in u or "/api/" in u: reqs.append((time.time(), r.request.method, u, r.status))
        except Exception: pass
    page.on("response", on_resp)
    page.add_init_script("window.__msgs=[];window.addEventListener('message',e=>{try{window.__msgs.push({origin:e.origin,data:e.data,t:Date.now()})}catch(_){}});")

    t0 = time.time()
    page.goto(START, wait_until="domcontentloaded", timeout=60000)
    note("start DOMContentLoaded in", round(time.time()-t0, 2), "s")
    try: page.wait_for_load_state("networkidle", timeout=20000)
    except Exception: pass
    note("networkidle at", round(time.time()-t0, 2), "s ; url =", page.url)
    shot(page, "01_farq_home")
    els = page.locator("text=تسعير")
    note("elements with text 'تسعير':", els.count())
    for i in range(min(els.count(), 6)):
        try:
            e = els.nth(i); bb = e.bounding_box()
            note("  ", i, repr(e.inner_text()[:60]), "visible=", e.is_visible(), "box=", bb, "tag=", e.evaluate("e=>e.tagName+'#'+(e.id||'')+'.'+String(e.className).slice(0,60)"))
        except Exception as ex: note("  err", ex)
    clicked = False; t1 = time.time()
    if "/taseer" not in page.url:
        for i in range(els.count()):
            e = els.nth(i)
            if e.is_visible():
                t1 = time.time(); e.click(); clicked = True; break
        if not clicked:
            note("NO VISIBLE TASEER ENTRY on first paint; navigating to /taseer"); t1 = time.time(); page.goto("https://www.farq.sa/taseer")
        page.wait_for_url("**/taseer**", timeout=30000)
        note("URL after tap:", page.url, "in", round(time.time()-t1, 2), "s")
        shot(page, "02_after_tap_immediate")
    TOP = "taseer.farq.sa" in START
    if not TOP:
        page.wait_for_selector("iframe[data-testid=taseer-embed]", timeout=30000)
        note("iframe element at", round(time.time()-t1, 2), "s")
    class FL:
        def __init__(self, page): self.fl = page.frame_locator("iframe[data-testid=taseer-embed]"); self.page = page
        def locator(self, sel): return self.fl.locator(sel)
        def fill(self, sel, v): self.fl.locator(sel).fill(v)
        def wait_for_selector(self, sel, timeout=30000): self.fl.locator(sel).first.wait_for(timeout=timeout)
        @property
        def url(self):
            try: return self.page.locator("iframe[data-testid=taseer-embed]").evaluate("e=>{try{return e.contentWindow.location.href}catch(_){return 'x-origin'}}")
            except Exception: return "?"
    class TL:
        def __init__(self, page): self.page = page
        def locator(self, sel): return self.page.locator(sel)
        def fill(self, sel, v): self.page.fill(sel, v)
        def wait_for_selector(self, sel, timeout=30000): self.page.wait_for_selector(sel, timeout=timeout)
        @property
        def url(self): return self.page.url
    frame = TL(page) if TOP else FL(page)
    frame.wait_for_selector("#composer-query", timeout=30000)
    note("Taseer composer ready at", round(time.time()-t1, 2), "s after tap")
    shot(page, "03_taseer_home")
    ifr = page.locator("iframe[data-testid=taseer-embed]")
    note("iframe box:", ifr.bounding_box() if ifr.count() else None, "viewport:", VIEW)
    cta = frame.locator("button[form=composer]")
    note("CTA visible:", cta.is_visible(), "box:", cta.bounding_box())
    note("home headline:", frame.locator("h1").first.inner_text())
    frame.fill("#composer-query", QUERY)
    shot(page, "04_typed")
    cta.scroll_into_view_if_needed(); t2 = time.time(); cta.click()
    time.sleep(0.15); shot(page, "05_after_cta_150ms")
    outcome = None
    for _ in range(200):
        try: txt = frame.locator("h1").first.inner_text()
        except Exception: txt = ""
        if any(k in txt for k in ("فهمناه", "مدينة", "المدينة", "ما فهمنا", "توضيح", "ما قدرنا")):
            outcome = txt; break
        time.sleep(0.1)
    note("after CTA ->", repr(outcome), "in", round(time.time()-t2, 2), "s ; frame url:", frame.url)
    shot(page, "06_after_cta")
    if outcome and ("مدينة" in outcome or "المدينة" in outcome):
        btns = frame.locator("button[data-action]")
        note("city-ask actions:", sorted(set(btns.nth(i).get_attribute("data-action") for i in range(btns.count()))))
        frame.locator("button:has-text('الرياض')").first.click()
        txt = ""
        for _ in range(100):
            try: txt = frame.locator("h1").first.inner_text()
            except Exception: txt = ""
            if "فهمناه" in txt: break
            time.sleep(0.1)
        note("after city ->", repr(txt)); shot(page, "07_after_city")
    cards = frame.locator(".fq-needcard")
    note("need cards:", cards.count())
    for i in range(cards.count()):
        note("  need", i, "=>", repr(cards.nth(i).inner_text().replace("\n", " | ")))
    rs = frame.locator("button[data-action=run-search]")
    note("run-search visible:", rs.is_visible(), "box:", rs.bounding_box(), "disabled:", rs.get_attribute("disabled"))
    t3 = time.time(); rs.click()
    time.sleep(0.2); shot(page, "08_searching_200ms")
    first_result = None; done = None
    for i in range(450):
        n = frame.locator(".fq-result").count()
        if n and first_result is None:
            first_result = round(time.time()-t3, 2); note("first results on screen at", first_result, "s (", n, "cards )"); shot(page, "09_first_results")
        body = frame.locator(".fq-body").first.inner_text() if frame.locator(".fq-body").count() else ""
        if first_result and "البحث مستمر" not in body and "ندور" not in body and "يبحث فرق" not in body:
            done = round(time.time()-t3, 2); break
        if not first_result and any(k in body for k in ("ما لقينا", "خلل", "طوّل", "ما فهمنا")):
            done = round(time.time()-t3, 2); break
        time.sleep(0.2)
    n = frame.locator(".fq-result").count()
    note("search settled at", done, "s ; results =", n, "; url:", frame.url)
    shot(page, "10_results_done")
    try: note("found line:", frame.locator(".fq-live").first.inner_text())
    except Exception: pass
    names = [frame.locator(".fq-offerwho").nth(i).inner_text() for i in range(min(n, 60))]
    note("seller name dups among", len(names), ":", {x for x in names if names.count(x) > 1})
    titles = [frame.locator(".fq-offertitle").nth(i).inner_text() for i in range(min(n, 60))]
    note("first 8 titles:", titles[:8])
    note("technical junk text:", [t for t in titles + names if any(b in t for b in ("undefined", "null", "[object", "NaN"))])
    pills = frame.locator(".fq-fpill"); note("need filter pills:", [pills.nth(i).inner_text() for i in range(pills.count())])
    if n:
        frame.locator("button[data-action=toggle]").nth(0).click(); time.sleep(0.05)
        note("after 1st select: picked cards =", frame.locator(".fq-result.picked").count(), "sticky:", frame.locator(".fq-sticky").count())
        if n > 1: frame.locator("button[data-action=toggle]").nth(1).click()
        shot(page, "11_selected")
        rv = frame.locator("button[data-action=review]")
        note("review CTA text:", rv.inner_text() if rv.count() else None, "box:", rv.bounding_box() if rv.count() else None)
        rv.click(); time.sleep(0.3); shot(page, "12_review")
        note("review url:", frame.url, "h1:", frame.locator("h1").first.inner_text())
        send = frame.locator("button[data-action=send]")
        note("send button:", send.inner_text(), "disabled:", send.get_attribute("disabled"), "box:", send.bounding_box())
        send.click(); time.sleep(1.0); shot(page, "13_after_send_tap")
        note("frame after send tap: url", frame.url, "h1:", frame.locator("h1").first.inner_text() if frame.locator("h1").count() else None)
        msgs = page.evaluate("window.__msgs")
        note("postMessages to Farq:", [m for m in msgs if isinstance(m.get('data'), dict) and m['data'].get('source') == 'taseer'])
        d = page.locator("[role=dialog]"); note("Farq dialogs:", d.count(), [d.nth(i).inner_text()[:100].replace("\n", " | ") for i in range(d.count())])
        time.sleep(1.0); shot(page, "14_send_state_2s")
        note("--- reload test ---")
        page.reload(wait_until="domcontentloaded")
        frame = TL(page) if TOP else FL(page)
        try:
            frame.wait_for_selector(".fq-body", timeout=20000); time.sleep(0.8)
            note("after reload: frame url", frame.url, "h1:", frame.locator("h1").first.inner_text(), "composer:", repr(frame.locator("#composer-query").input_value() if frame.locator("#composer-query").count() else None), "results:", frame.locator(".fq-result").count(), "needcards:", frame.locator(".fq-needcard").count())
        except Exception as ex: note("after reload err", ex)
        shot(page, "15_after_reload")
        note("--- back test ---")
        page.go_back(); time.sleep(1.0)
        note("after Back: top url", page.url, "; frame h1:", (frame.locator("h1").first.inner_text() if frame.locator("h1").count() else None) if "/taseer" in page.url else "(left taseer)")
        shot(page, "16_after_back")
    note("--- console errors/warnings ---"); [note(c[:300]) for c in console[:40]]
    note("--- failed/4xx/5xx ---"); [note(f[:300]) for f in failed[:40]]
    note("--- API calls ---"); [note(round(t - t0, 1), m, u.replace('https://', '')[:120], s) for t, m, u, s in reqs[:60]]
    browser.close()
    note("DONE")
