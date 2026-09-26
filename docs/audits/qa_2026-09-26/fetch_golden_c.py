"""Fetch live production titles for set C: 20 more real Saudi queries (for hand labelling)."""
import json, time, urllib.request, sys
QUERIES = [
 "أبي طباخ للمناسبات", "أبي مصور أفراح", "أبي سيارة أجرة توصيل مطار", "أبي مدرس خصوصي رياضيات", "أبي جهاز رياضي مشي",
 "أبي كاميرا كانون", "أبي جوال سامسونج S23", "أبي أرض في الرياض شمال", "أبي فيلا للبيع", "أبي دينا نقل من الرياض للدمام",
 "أبي خادمة نقل كفالة", "أبي صيانة مكيف شباك", "أبي تصميم ديكور", "أبي عزل فوم سطح 200 متر", "أبي طباعة بروشورات 1000 نسخة",
 "أبي ذبيحة تيس", "أبي كرسي مكتب", "أبي ثلاجة صغيرة للمكتب", "أبي مكينة قهوة", "أبي هدايا تخرج",
]
out = []
for q in QUERIES:
    body = json.dumps({"query": q + " الرياض"}).encode()
    req = urllib.request.Request("https://taseer.farq.sa/v1/search", data=body, headers={"Content-Type": "application/json"})
    try:
        t = time.time(); d = json.load(urllib.request.urlopen(req, timeout=120)); dt = round(time.time() - t, 1)
    except Exception as e:
        print("ERR", q, e, flush=True); out.append({"query": q, "error": str(e), "titles": []}); continue
    res = d.get("results", []) + [r for g in d.get("groups", []) for r in g.get("results", [])]
    seen = set(); titles = []
    for r in res:
        t_ = (r.get("ad") or {}).get("title") or ""
        if t_ and t_ not in seen: seen.add(t_); titles.append({"title": t_, "body": ((r.get("ad") or {}).get("description") or "")[:160], "price": (r.get("ad") or {}).get("price_amount")})
        if len(titles) >= 12: break
    out.append({"query": q, "state": d.get("state"), "need": d.get("intent", {}).get("need"), "unit": d.get("intent", {}).get("result_unit"), "intent": d.get("intent"), "n": len(res), "secs": dt, "titles": titles})
    print(f"{q} | {d.get('state')} | need={d.get('intent',{}).get('need')} | unit={d.get('intent',{}).get('result_unit')} | n={len(res)} | {dt}s", flush=True)
    for t_ in titles[:12]: print("   -", t_["title"][:90], "|", t_["price"], flush=True)
json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else "/Users/m4pro/Farq-taseer-wt-golden-c/docs/audits/qa_2026-09-26/golden_live_2026-09-26_c.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("DONE", len(out))
