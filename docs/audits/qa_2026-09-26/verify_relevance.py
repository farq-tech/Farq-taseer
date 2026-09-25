import json, time, urllib.request
def search(q):
    req = urllib.request.Request("https://taseer.farq.sa/v1/search", data=json.dumps({"query": q}).encode(), headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=90)); res = d.get("results", []) + [r for g in d.get("groups", []) for r in g.get("results", [])]
    return [((r.get("ad") or {}).get("title") or "")[:70] for r in res]
for q, bad in (("أبي صيانة غسالة الرياض", ("تحتاج", "بها عطل", "نظيفه")), ("أبي بطارية سيارة 70 أمبير الرياض", ("150", "200", "92", "100 امبير", "اسكوتر", "48 فولت")), ("أبي كفرات 265/60 R18 الرياض", ("65R17", "65 18", "70 18", "75r16")), ("أبي مولد كهرباء 10 كيلو الرياض", ("18 كيلو", "120 كيلو", "35 كيلو", "50KVA"))):
    titles = search(q); wrong = [t for t in titles if any(b in t for b in bad)]
    print(q, "| results:", len(titles), "| still wrong:", len(wrong), wrong[:3], flush=True)
    for t in titles[:5]: print("   -", t, flush=True)
print("DONE")
