"""Error-state audit of the customer app: home → «ابدأ التسعير» → M02 → «ابحث عن الخيارات»
under each failure the network can produce. Every scenario is simulated with page.route;
nothing reaches production. Usage: python a11y_states.py [scenario ...] [--tag before|after]"""
import json, os, re, sys, time, urllib.parse
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://127.0.0.1:8796")
OUT = os.path.dirname(os.path.abspath(__file__)) + "/shots"; os.makedirs(OUT, exist_ok=True)
TAG = "after" if "--tag" in sys.argv and sys.argv[sys.argv.index("--tag") + 1] == "after" else "before"
WANT = [a for a in sys.argv[1:] if not a.startswith("--") and a not in ("before", "after")]
TECH = re.compile(r"undefined|null|\b[45]\d\d\b|Failed to fetch|TypeError|Error:|NaN|\[object", re.I)
REQUEST = "أبي سباك يصلح تسريب في المطبخ"

def note(*a): print(" ".join(str(x) for x in a), flush=True)
def shot(page, n): page.screenshot(path=f"{OUT}/a11y_{n}_{TAG}.png", full_page=False)
def text(page): return re.sub(r"\s+", " ", page.locator("main, #app").first.inner_text()).strip()

def new_page(ctx):
    page = ctx.new_page(); page.set_default_timeout(20000)
    errors = []; page.on("pageerror", lambda e: errors.append(str(e))); page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.errors = errors
    return page

def open_home(page):
    page.goto(BASE + "/?embed=1", wait_until="domcontentloaded")
    page.wait_for_selector("#composer-query")
    page.wait_for_function("document.querySelectorAll('.fq-tab').length === 3", timeout=10000)

def to_m02(page):
    page.fill("#composer-query", REQUEST)
    page.locator("button[form=composer]").click()

def to_search(page):
    page.wait_for_selector(".fq-needcard")
    if page.locator("[data-action=need-city]").count():
        page.locator("button[data-action=need-city]:has-text('الرياض')").first.click(); time.sleep(0.2)
    page.locator("button[data-action=run-search]").click()

def settle(page, limit=40, name=""):
    """wait until the app stopped searching (spinner gone or error/empty card shown)"""
    t = time.time(); hinted = False
    while time.time() - t < limit:
        body = text(page)
        if name and not hinted and time.time() - t > 14:
            # what the customer reads while a slow search is still running
            hinted = True; note(f"[{name}] at 14s: back button: {page.locator('.fq-embar-back').count()} | slow line: {'وقت أطول' in body}"); shot(page, name + "_waiting")
        if page.locator("[data-action=retry-search], [data-action=retry-intent], .fq-result, #intent-answer").count() and "يبحث فرق" not in body:
            return round(time.time() - t, 1)
        if page.locator("[role=alert]").count() and "يبحث فرق" not in body:
            return round(time.time() - t, 1)
        time.sleep(0.4)
    return None

def report(page, name, waited):
    body = text(page)
    spinner = "يبحث فرق" in body or page.locator(".fq-skel").count() > 0
    retry = page.locator("[data-action=retry-search], [data-action=retry-intent]").count()
    tech = TECH.findall(body)
    alert = page.locator("[role=alert]").count()
    note(f"[{name}] settled after {waited}s | spinner still: {spinner} | retry button: {retry} | role=alert: {alert} | technical text: {tech}")
    note(f"[{name}] screen: {body[:420]}")
    shot(page, name)
    return body

def stream_500(route): route.fulfill(status=500, content_type="application/json", body='{"detail":"Internal Server Error"}')
def stream_429(route): route.fulfill(status=429, content_type="application/json", headers={"Retry-After": "60"}, body='{"detail":"too many searches, try again in a minute"}')
def stream_net(route): route.abort("connectionreset")
PENDING = []
def stream_hang(route): PENDING.append(route)  # never answered → the app's own 30s timeout must fire
def stream_drop(route):
    """Real stream, cut after the first results batch and a half-written line: the connection died."""
    resp = route.fetch(); body = resp.text()
    lines = body.split("\n"); keep = []
    for line in lines:
        keep.append(line)
        if '"type": "results"' in line: break
    cut = "\n".join(keep) + '\n{"type": "results", "resu'
    route.fulfill(status=200, content_type="application/x-ndjson", body=cut)

SCENARIOS = {
    "search_500": {"stream": stream_500, "plain": stream_500},
    "search_500_fallback_ok": {"stream": stream_500},
    "search_timeout": {"stream": stream_hang},
    "search_429": {"stream": stream_429, "plain": stream_429},
    "search_netdrop": {"stream": stream_net, "plain": stream_net},
    "search_midstream": {"stream": stream_drop},
    "intent_500": {"intent": stream_500},
    "intent_429": {"intent": stream_429},
    "intent_net": {"intent": stream_net},
}

def run(ctx, name, spec):
    page = new_page(ctx)
    if "stream" in spec: page.route("**/v1/search/stream", spec["stream"])
    if "plain" in spec: page.route("**/v1/search", spec["plain"])
    if "intent" in spec: page.route("**/v1/intent", spec["intent"])
    open_home(page); to_m02(page)
    if "intent" in spec:
        page.wait_for_selector("[data-action=retry-intent], .fq-needcard, #intent-answer", timeout=15000)
        body = report(page, name, 0)
        # draft: the typed sentence is still on screen
        note(f"[{name}] draft on screen: {REQUEST in body}")
        # retry with the network back
        page.unroute("**/v1/intent")
        if page.locator("[data-action=retry-intent]").count():
            page.locator("[data-action=retry-intent]").click(); page.wait_for_selector(".fq-needcard", timeout=15000)
            note(f"[{name}] retry → M02 cards: {page.locator('.fq-needcard').count()}")
    else:
        to_search(page)
        waited = settle(page, 45, name)
        body = report(page, name, waited)
        page.unroute("**/v1/search/stream")
        if "plain" in spec: page.unroute("**/v1/search")
        if page.locator("[data-action=retry-search]").count():
            page.locator("[data-action=retry-search]").click()
            try:
                page.wait_for_selector(".fq-result", timeout=60000)
                note(f"[{name}] retry → results: {page.locator('.fq-result').count()}")
            except Exception:
                note(f"[{name}] retry → NO results; screen: {text(page)[:200]}")
            shot(page, name + "_retry")
        # the draft survives: M02 still lists the items
        if page.locator("[data-action=back-understand]").count():
            page.locator("[data-action=back-understand]").first.click(); time.sleep(0.3)
            note(f"[{name}] back to M02 → cards: {page.locator('.fq-needcard').count()} | request line kept: {page.locator('#composer-query').count() == 0}")
    if page.errors: note(f"[{name}] js errors: {page.errors[:5]}")
    page.close()

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar-SA")
    ctx.add_cookies([{"name": "farq-auth.2.c0", "value": urllib.parse.quote(json.dumps({"access_token": "farq-good"})), "domain": "127.0.0.1", "path": "/"}])
    for name, spec in SCENARIOS.items():
        if WANT and name not in WANT: continue
        try: run(ctx, name, spec)
        except Exception as e: note(f"[{name}] CRASH: {e}")
    b.close(); note("DONE")
