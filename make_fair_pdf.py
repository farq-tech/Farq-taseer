"""
Rebuild the stress booklet from the ground truth with a REAL text layer.

The pack's PDF was drawn letter-reversed and unshaped (DejaVu, no bidi), which
no booklet a buyer uploads looks like: Word, Etimad and every browser write
logical-order Arabic. Chrome prints this HTML with the same kind of text layer
those tools produce, so what is graded is the reader, not the generator.

Every trap of the pack survives: three layouts (A Etimad-like, B Farq DC/SITE,
C bare), wrapped descriptions, the 301-306 numbering gap, the SITE- description
anomaly, presentation-form and zero-width characters, Arabic-Indic and
thousands-separated quantities, empty and impossible quantities, empty units,
section totals and carried sums inside the table, a page whose header is painted
twice, and page furniture in the data area.
"""
import json, html, sys, itertools

src = sys.argv[1]
out = sys.argv[2]
d = json.load(open(src))
items = d["items"]

ROWS = 34
QTY_PRINT = {
    "T_QTY_ARABIC_3_500": "٣٫٥٠٠",
    "T_QTY_1250_5": "1,250.5",
    "T_QTY_AR_DECIMAL": "12,75",
    "T_SEND_GUARD_70": "",
    "T_SEND_GUARD_71": "0",
    "T_SEND_GUARD_72": "حسب المخطط",
    "T_SEND_GUARD_73": "كمية غير قابلة للجزم",
}

def qty_text(it):
    t = it.get("trap_id")
    if t in QTY_PRINT:
        return QTY_PRINT[t]
    q = it["expected_quantity"]
    if q is None:
        return ""
    if isinstance(q, (int, float)):
        return str(int(q)) if float(q).is_integer() else str(q)
    return str(q)

def desc_html(it):
    return html.escape(it["raw_description"]).replace("\n", "<br>")

# pages: consecutive runs of one layout, ROWS rows each
pages = []
for layout, grp in itertools.groupby(items, key=lambda i: i["layout"]):
    grp = list(grp)
    for k in range(0, len(grp), ROWS):
        pages.append((layout, grp[k:k + ROWS]))
total_pages = len(pages)

HEAD = {
    "A": ["رقم", "الرمز الإنشائي", "الوصف", "الوحدة", "الكمية", "ملاحظات"],
    "B": ["الرقم", "الرمز", "الفئة", "البند", "المواصفة المختصرة", "الكمية", "الوحدة"],
    "C": ["الوصف", "الوحدة", "الكمية"],
}

def row_html(layout, it):
    seq = "" if it["sequence"] is None else str(it["sequence"])
    code = it.get("structural_code") or ""
    q = html.escape(qty_text(it))
    u = html.escape(it["raw_unit"] or "")
    if layout == "A":
        cells = [seq, code, desc_html(it), u, q, ""]
    elif layout == "B":
        spec = "مطابق للمواصفات" if it["sequence"] and it["sequence"] % 3 == 0 else ""
        cells = [seq, code, html.escape(it["section"]), desc_html(it), spec, q, u]
    else:
        cells = [desc_html(it), u, q]
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

def total_rows(layout, n):
    span = len(HEAD[layout])
    return (
        f'<tr class="tot"><td colspan="{span - 1}">مجموع القسم</td><td class="num">{n:,}</td></tr>'
        f'<tr class="tot"><td colspan="{span - 1}">ما يُرحّل</td><td class="num">{n * 3 + 111:,}</td></tr>'
    )

parts = []
for pi, (layout, rows) in enumerate(pages, start=1):
    head_tr = "<tr>" + "".join(f"<th>{h}</th>" for h in HEAD[layout]) + "</tr>"
    thead = head_tr + (head_tr if pi == 28 else "")  # page 28: thead painted twice
    body = "".join(row_html(layout, it) for it in rows)
    if pi % 10 == 0:
        body += total_rows(layout, 987654 + pi)
    furniture = ""
    if pi % 7 == 0:
        # page furniture sitting inside the data area
        furniture = f'<div class="furn">صفحة {pi} من {total_pages} &nbsp;&nbsp; يتبع</div>'
    parts.append(
        f'<section class="page"><div class="hdr"><span>كراسة الكميات — مشروع الاختبار الشامل</span>'
        f'<span>القسم: {html.escape(rows[0]["section"])} · مدينة التسليم: {html.escape(rows[0]["delivery_city"])}</span></div>'
        f'<table><thead>{thead}</thead><tbody>{body}</tbody></table>{furniture}'
        f'<div class="foot"><span>{pi} / {total_pages}</span><span>رقم الكراسة 2026-STRESS-1650</span></div></section>'
    )

doc = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<style>
@page {{ size: A4 landscape; margin: 10mm; }}
body {{ font-family: "Geeza Pro", "Arial", "Tahoma", sans-serif; font-size: 9.5pt; color: #111; margin: 0; }}
.page {{ break-after: page; page-break-after: always; }}
.hdr {{ display: flex; justify-content: space-between; font-weight: 700; margin-bottom: 4mm; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ border: 0.3pt solid #999; padding: 1.2mm 2mm; text-align: right; vertical-align: top; }}
th {{ background: #eee; font-weight: 700; }}
td.num {{ text-align: left; direction: ltr; }}
tr.tot td {{ font-weight: 700; background: #f7f7f7; }}
.furn {{ margin-top: 2mm; font-size: 8pt; color: #555; }}
.foot {{ display: flex; justify-content: space-between; font-size: 8pt; color: #555; margin-top: 3mm; }}
</style></head><body>{''.join(parts)}</body></html>"""
open(out, "w", encoding="utf-8").write(doc)
print("pages", total_pages, "rows", len(items))
