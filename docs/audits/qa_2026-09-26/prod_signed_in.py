"""Production, signed in with the Farq QA account: the frame must receive the session and the
journey must reach an enabled send button. The send itself is NOT pressed (real suppliers)."""
import json, os, sys, time
from playwright.sync_api import sync_playwright
OUT = os.path.dirname(os.path.abspath(__file__)) + "/shots"
acct = json.load(open("/tmp/taseer-local/qa_account.json"))
def note(*a): print(" ".join(str(x) for x in a), flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
    page = ctx.new_page(); page.set_default_timeout(20000)
    errs = []; page.on("pageerror", lambda e: errs.append(str(e)))
    fails = []; page.on("response", lambda r: fails.append(f"{r.status} {r.request.method} {r.url[:100]}") if r.status >= 400 else None)
    page.goto("https://www.farq.sa/taseer?signin=1", wait_until="domcontentloaded")
    page.wait_for_selector("[role=dialog]", timeout=30000); note("A. /taseer?signin=1 opened the Farq sign-in modal"); time.sleep(0.5)
    page.screenshot(path=f"{OUT}/prod_s_01_modal.png")
    if not page.locator("#auth-signin-email").is_visible():
        for label in ("البريد الإلكتروني", "البريد", "Email"):
            btn = page.locator(f"[role=dialog] button:has-text('{label}'), [role=dialog] [role=tab]:has-text('{label}')")
            if btn.count(): btn.first.click(); break
        time.sleep(0.4)
    dlgs = page.locator("[role=dialog]"); note("   dialogs:", dlgs.count(), [dlgs.nth(i).inner_text()[:120].replace("\n", " | ") for i in range(dlgs.count())])
    if not page.locator("#auth-signin-email").is_visible():
        btns = page.locator("[role=dialog] button"); note("   dialog buttons:", [btns.nth(i).inner_text()[:25] for i in range(btns.count())])
        for i in range(btns.count()):
            if "بريد" in btns.nth(i).inner_text() or "Email" in btns.nth(i).inner_text(): btns.nth(i).click(); time.sleep(0.5); break
    page.wait_for_selector("#auth-signin-email", timeout=10000)
    page.fill("#auth-signin-email", acct["email"]); page.fill("#auth-signin-password", acct["password"])
    submit = page.locator("[role=dialog] button[type=submit]"); note("   submit buttons:", [submit.nth(i).inner_text()[:25] for i in range(submit.count())])
    submit.first.click()
    for _ in range(60):
        if not page.locator("#auth-signin-email").count(): break
        time.sleep(0.5)
    if page.locator("#auth-signin-email").count():
        page.screenshot(path=f"{OUT}/prod_s_01b_modal_after.png"); note("   modal still open:", page.locator("[role=dialog]").last.inner_text()[:300].replace("\n", " | "))
        raise SystemExit("sign-in did not complete")
    note("B. signed in to Farq (modal closed)")
    fl = page.frame_locator("iframe[data-testid=taseer-embed]")
    t = time.time()
    page.wait_for_function("""() => { const f = document.querySelector('iframe[data-testid=taseer-embed]'); return !!f; }""")
    fl.locator(".fq-tab").nth(2).wait_for(timeout=20000)
    note("C. Taseer frame received the Farq session: nav tabs =", fl.locator(".fq-tab").count(), "after", round(time.time()-t, 1), "s")
    page.screenshot(path=f"{OUT}/prod_s_02_frame_signed_in.png")
    ck = page.locator("button:has-text('قبول')")
    if ck.count(): note("   (cookie banner covers the frame bottom; accepting it as a user would)"); ck.first.click(); time.sleep(0.5)
    fl.locator("[data-action=account]").first.click(); time.sleep(1.2)
    acc = fl.locator("main").inner_text(); note("D. حسابي shows QA email:", acct["email"] in acc, "| unlimited/trial line:", [l for l in acc.split("\n") if "بند" in l][:2])
    page.screenshot(path=f"{OUT}/prod_s_03_account.png")
    fl.locator("[data-action=home]").first.click(); time.sleep(0.5)
    fl.locator("#composer-query").fill("أبي تلفزيون سامسونج 65 بوصة")
    fl.locator("button[form=composer]").scroll_into_view_if_needed(); fl.locator("button[form=composer]").click()
    fl.locator("button[data-action=search-city]").first.wait_for(); fl.locator("button[data-action=search-city]:has-text('الرياض')").first.click()
    fl.locator(".fq-needcard").first.wait_for(); note("E. M02 cards:", fl.locator(".fq-needcard").count(), "=>", fl.locator(".fq-needcard").first.inner_text().replace("\n", " | "))
    page.screenshot(path=f"{OUT}/prod_s_04_understand.png")
    fl.locator("button[data-action=run-search]").click()
    t = time.time(); fl.locator(".fq-result").first.wait_for(timeout=60000); note("F. first result after", round(time.time()-t, 1), "s")
    for _ in range(150):
        if "البحث مستمر" not in fl.locator(".fq-body").first.inner_text(): break
        time.sleep(0.3)
    n = fl.locator(".fq-result").count(); titles = [fl.locator(".fq-offertitle").nth(i).inner_text() for i in range(min(n, 6))]
    note("   results:", n, "| first titles:", titles)
    page.screenshot(path=f"{OUT}/prod_s_05_results.png")
    fl.locator("button[data-action=toggle]").nth(0).click(); fl.locator("button[data-action=review]").click()
    fl.locator("button[data-action=send]").wait_for(); send = fl.locator("button[data-action=send]")
    note("G. review: send button enabled =", send.get_attribute("disabled") is None, "(NOT pressed: real suppliers)")
    page.screenshot(path=f"{OUT}/prod_s_06_review.png")
    page.reload(wait_until="domcontentloaded"); fl = page.frame_locator("iframe[data-testid=taseer-embed]")
    fl.locator(".fq-body").first.wait_for(timeout=30000); time.sleep(1.5)
    h1 = fl.locator("h1").first.inner_text(); note("H. after reload the frame shows:", repr(h1), "| tabs:", fl.locator(".fq-tab").count(), "| send present:", fl.locator("button[data-action=send]").count())
    page.screenshot(path=f"{OUT}/prod_s_07_after_reload.png")
    # the stray coming-soon badge on the Farq home
    page.goto("https://www.farq.sa/", wait_for="domcontentloaded") if False else page.goto("https://www.farq.sa/", wait_until="domcontentloaded"); time.sleep(2)
    soon = page.locator(".sector-tab__soon")
    for i in range(soon.count()):
        el = soon.nth(i); tab = el.locator("xpath=..")
        note("I. coming-soon badge", i, "visible:", el.is_visible(), "badge box:", el.bounding_box(), "tab box:", tab.bounding_box())
    note("--- js errors:", errs[:5]); note("--- 4xx/5xx:", fails[:10])
    b.close(); note("DONE")
