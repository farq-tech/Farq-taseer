"""Fetch live production titles for a fresh set of real Saudi queries (for hand labelling)."""
import json, time, urllib.request, sys
QUERIES = [
 "أبي غسالة LG 10 كيلو", "أبي ايفون 13 مستعمل", "أبي شقة 3 غرف للإيجار", "أبي مكيف سبليت 2 طن", "أبي لابتوب للدراسة",
 "أبي كامري 2015 مستعملة", "أبي بلايستيشن 5", "أبي طاولة طعام 8 كراسي", "أبي شاشة 55 بوصة", "أبي ايباد للأطفال",
 "أبي سباك في جدة يصلح سخان", "أبي كهربائي بالدمام", "أبي نقل عفش داخل جدة", "أبي معلم دهان في الخبر", "أبي مقاول ترميم حمام",
 "أبي غرفة نوم كاملة", "أبي خيمة للبر", "أبي مولد كهرباء 5 كيلو صامت", "أبي بطارية طاقة شمسية 200 أمبير", "أبي دراجة هوائية للكبار",
]
out = []
for q in QUERIES:
    body = json.dumps({"query": q + " الرياض"}).encode()
    req = urllib.request.Request("https://taseer.farq.sa/v1/search", data=body, headers={"Content-Type": "application/json"})
    try:
        t = time.time(); d = json.load(urllib.request.urlopen(req, timeout=90)); dt = round(time.time() - t, 1)
    except Exception as e:
        print("ERR", q, e, flush=True); continue
    res = d.get("results", []) + [r for g in d.get("groups", []) for r in g.get("results", [])]
    seen = set(); titles = []
    for r in res:
        t_ = (r.get("ad") or {}).get("title") or ""
        if t_ and t_ not in seen: seen.add(t_); titles.append({"title": t_, "body": ((r.get("ad") or {}).get("description") or "")[:160], "price": (r.get("ad") or {}).get("price_amount")})
        if len(titles) >= 12: break
    out.append({"query": q, "state": d.get("state"), "need": d.get("intent", {}).get("need"), "unit": d.get("intent", {}).get("result_unit"), "n": len(res), "secs": dt, "titles": titles})
    print(f"{q} | {d.get('state')} | need={d.get('intent',{}).get('need')} | n={len(res)} | {dt}s", flush=True)
    for t_ in titles[:12]: print("   -", t_["title"][:80], "|", t_["price"], flush=True)
json.dump(out, open("/Users/m4pro/Farq-taseer/docs/audits/qa_2026-09-26/golden_live_2026-09-26_b.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("DONE", len(out))
