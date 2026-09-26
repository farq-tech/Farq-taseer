"""axe-core over the customer screens: home, M02, results, review, requests, thread.
Usage: python a11y_axe.py [--tag before|after]. Prints violations per screen (id × nodes)."""
import json, os, re, sys, time, urllib.parse
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://127.0.0.1:8796")
HERE = os.path.dirname(os.path.abspath(__file__)); OUT = HERE + "/shots"; os.makedirs(OUT, exist_ok=True)
TAG = "after" if "--tag" in sys.argv and sys.argv[sys.argv.index("--tag") + 1] == "after" else "before"
AXE = os.environ.get("AXE_JS", "/private/tmp/claude-501/-Users-m4pro-farq-repo-farq/bb331154-7d24-4b36-98a2-36e7e8664972/scratchpad/axe.min.js")
REQUEST = "أبي سباك يصلح تسريب في المطبخ"
VERBOSE = "--verbose" in sys.argv

def note(*a): print(" ".join(str(x) for x in a), flush=True)

AXE_SRC = open(AXE, encoding="utf-8").read()

def audit(page, name):
    time.sleep(1.2)  # the screens' entrance animation fades cards in; contrast is read once it has ended
    # the app's CSP is script-src 'self': axe is served from a same-origin path instead of inline
    if not page.evaluate("() => Boolean(window.axe)"):
        page.route("**/__axe.min.js", lambda route: route.fulfill(status=200, content_type="application/javascript", body=AXE_SRC))
        page.add_script_tag(url="/__axe.min.js"); page.wait_for_function("() => Boolean(window.axe)")
    result = page.evaluate("""async () => {
        const r = await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } });
        return r.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.map(n => ({ target: n.target.join(' '), summary: n.failureSummary.split('\\n').slice(0,3).join(' | '), html: n.html.slice(0, 140) })) }));
    }""")
    # small targets: WCAG 2.5.8 wants 24px; the audit asks for 44px on every tappable control
    small = page.evaluate("""() => [...document.querySelectorAll('button, a[href], input, [role=button], [role=tab], select, textarea')]
        .filter(el => el.offsetParent !== null && !el.disabled)
        .map(el => { const r = el.getBoundingClientRect(); return { tag: el.tagName.toLowerCase(), cls: el.className, text: (el.getAttribute('aria-label') || el.innerText || el.placeholder || '').trim().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height) }; })
        .filter(x => (x.w < 44 || x.h < 44) && x.w > 0 && x.h > 0)""")
    total = sum(len(v["nodes"]) for v in result)
    note(f"== {name}: {len(result)} axe rules / {total} nodes | controls under 44px: {len(small)}")
    for v in result:
        note(f"   - {v['id']} [{v['impact']}] ×{len(v['nodes'])}: {v['help']}")
        for n in v["nodes"][: (10 if VERBOSE else 3)]: note(f"       {n['target']} :: {n['html']}")
    for s in small[: (30 if VERBOSE else 8)]: note(f"   · small {s['w']}x{s['h']} <{s['tag']} .{s['cls']}> {s['text']}")
    page.screenshot(path=f"{OUT}/a11y_axe_{name}_{TAG}.png")
    return {"rules": len(result), "nodes": total, "small": len(small), "ids": [v["id"] for v in result]}

summary = {}
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA", bypass_csp=True)
    ctx.add_cookies([{"name": "farq-auth.2.c0", "value": urllib.parse.quote(json.dumps({"access_token": "farq-good"})), "domain": "127.0.0.1", "path": "/"}])
    page = ctx.new_page(); page.set_default_timeout(20000)
    page.goto(BASE + "/?embed=1", wait_until="domcontentloaded")
    page.wait_for_selector("#composer-query"); page.wait_for_function("document.querySelectorAll('.fq-tab').length === 3")
    time.sleep(0.5); summary["home"] = audit(page, "home")
    page.fill("#composer-query", REQUEST); page.locator("button[form=composer]").click()
    page.wait_for_selector(".fq-needcard")
    if page.locator("[data-action=need-city]").count(): page.locator("button[data-action=need-city]:has-text('الرياض')").first.click()
    time.sleep(0.4); summary["m02"] = audit(page, "m02")
    page.locator("button[data-action=run-search]").click()
    page.wait_for_selector(".fq-result", timeout=60000)
    for _ in range(150):
        if "البحث مستمر" not in page.locator("main").inner_text(): break
        time.sleep(0.3)
    time.sleep(0.5); summary["results"] = audit(page, "results")
    page.locator("button[data-action=toggle]").nth(0).click(); page.locator("button[data-action=toggle]").nth(1).click()
    page.locator("button[data-action=review]").click(); page.wait_for_selector("button[data-action=send]")
    time.sleep(0.4); summary["review"] = audit(page, "review")
    # requests tab (the bottom bar lives on the home screen)
    for _ in range(4):  # the draft restores the review on a reload, so walk back through the screens
        if page.locator(".fq-tab").count() == 3: break
        page.locator(".fq-embar-back").first.click(); time.sleep(0.4)
    page.locator(".fq-tab[data-action=requests]").click()
    page.wait_for_selector("[data-action=thread], .fq-body", timeout=15000); time.sleep(0.8)
    summary["requests"] = audit(page, "requests")
    if page.locator("[data-action=thread]").count():
        page.locator("[data-action=thread]").first.click(); page.wait_for_selector(".fq-composer, #user-reply", timeout=15000); time.sleep(0.8)
        summary["thread"] = audit(page, "thread")
    else:
        note("== thread: no request in the account to open")
    b.close()
note("SUMMARY", json.dumps(summary, ensure_ascii=False))
json.dump(summary, open(f"{HERE}/a11y_axe_{TAG}.json", "w"), ensure_ascii=False, indent=1)
