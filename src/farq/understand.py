"""Reading what the customer actually asked for.

The rules in intent.py recognise a hand-written table of heads and drop everything else into
a long tail that keeps the first four words and settles for two thirds of them. That is why
"أبي واحد يركب لي كاميرات مراقبة" and "أبي كاميرات مراقبة" reduced to the same need and
returned the same cameras for sale, and why "سبّاك وكهربائي" answered a request to build a
room: two of three words is enough to pass.

A model reads the sentence instead. It never widens what the rules found on its own - it
replaces the fields the rules are weakest at (the need, whether he is hiring or buying, the
words a listing must contain, and what to type into Haraj) and only when it returns all of
them in the shape asked for. Anything else - no key, a timeout, a bad body, a field of the
wrong type - and the caller keeps exactly the intent the rules produced. The search must
never fail because this step did.

Off by default: with no API key the rules run alone, exactly as before.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import OrderedDict

import httpx

from farq.contracts import IntentResponse, ResultUnit
from farq.text import normalize

log = logging.getLogger("farq.understand")

ENDPOINT = "https://api.anthropic.com/v1/messages"
# Extraction, not writing: the small fast model is the right one, and search already waits
# on Haraj, so this must not add meaningfully to that.
MODEL = os.environ.get("FARQ_UNDERSTAND_MODEL", "claude-haiku-4-5-20251001")
TIMEOUT_SECONDS = float(os.environ.get("FARQ_UNDERSTAND_TIMEOUT", "4"))
MAX_NEEDS = 4
MAX_TERMS = 4
CACHE_SIZE = 500

PROMPT = """أنت تقرأ طلب عميل سعودي يريد تسعيرة، وتخرج وصفًا منظّمًا له.

اقرأ نية العميل من الجملة كاملة، باللهجة التي كتبها.

أخرج JSON فقط، بهذا الشكل بالضبط:
{"needs": [{"need": "...", "hiring": true, "must_include": [["..."]], "search_terms": ["..."]}]}

لكل حاجة:
- need: اسم المطلوب بكلمتين أو ثلاث، بلا أفعال وبلا "أبي" وبلا اسم المدينة.
- hiring: true إذا كان يريد شخصًا ينفّذ عملًا (يركّب، يصلح، يبني، ينقل، يفصّل).
         false إذا كان يريد شراء شيء جاهز.
- must_include: مجموعات كلمات. الإعلان المناسب يجب أن يحتوي كلمة واحدة على الأقل من كل
  مجموعة. ضع المرادفات والأخطاء الإملائية الشائعة داخل المجموعة الواحدة.
- search_terms: من ١ إلى ٤ عبارات تُكتب في بحث حراج للعثور على المورد. عبارات يكتبها
  الناس فعلًا في إعلاناتهم، لا جملة العميل.

إذا طلب العميل أكثر من شيء مختلف، اجعل كل واحد حاجة مستقلة. وإلا فحاجة واحدة.

مثال:
الطلب: "ابي واحد يركب لي كاميرات مراقبة بالرياض"
{"needs": [{"need": "كاميرات مراقبة", "hiring": true,
  "must_include": [["كاميرا", "كاميرات"], ["مراقبه", "مراقبة", "cctv"]],
  "search_terms": ["تركيب كاميرات مراقبة", "فني كاميرات مراقبة"]}]}

الطلب: "%s"
"""

_cache: "OrderedDict[str, list[dict] | None]" = OrderedDict()
_lock = threading.Lock()


def _key() -> str:
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def enabled() -> bool:
    if (os.environ.get("FARQ_UNDERSTAND") or "").strip() == "0":
        return False
    return bool(_key())


def _cached(query: str):
    with _lock:
        if query in _cache:
            _cache.move_to_end(query)
            return True, _cache[query]
    return False, None


def _remember(query: str, value) -> None:
    with _lock:
        _cache[query] = value
        _cache.move_to_end(query)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


def _strings(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = " ".join(item.split())
            if text and text not in out:
                out.append(text)
    return out[:limit]


def _groups(value) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    out: list[list[str]] = []
    for group in value[:6]:
        words = _strings(group, 8) if isinstance(group, list) else ([group] if isinstance(group, str) else [])
        words = [normalize(word) for word in words]
        words = [word for word in dict.fromkeys(words) if word]
        if words:
            out.append(words)
    return out


def _parse(body: str) -> list[dict] | None:
    """The model is told to answer with JSON only; a stray sentence around it is still fine."""
    text = body.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    needs = data.get("needs") if isinstance(data, dict) else None
    if not isinstance(needs, list) or not needs:
        return None
    read: list[dict] = []
    for item in needs[:MAX_NEEDS]:
        if not isinstance(item, dict):
            continue
        need = item.get("need")
        if not isinstance(need, str) or not need.strip():
            continue
        groups = _groups(item.get("must_include"))
        terms = _strings(item.get("search_terms"), MAX_TERMS)
        if not groups or not terms:
            # Without both, the model has not replaced anything the rules do better.
            continue
        read.append({
            "need": " ".join(need.split()),
            "hiring": bool(item.get("hiring")),
            "must_include": groups,
            "search_terms": terms,
        })
    return read or None


def read_query(query: str) -> list[dict] | None:
    """The model's reading of the query, or None. Never raises."""
    if not enabled():
        return None
    folded = normalize(query)
    hit, value = _cached(folded)
    if hit:
        return value
    try:
        response = httpx.post(
            ENDPOINT,
            timeout=TIMEOUT_SECONDS,
            headers={
                "x-api-key": _key(),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 700,
                "temperature": 0,
                "messages": [{"role": "user", "content": PROMPT % query.replace('"', "'")}],
            },
        )
        if response.status_code != 200:
            log.warning("understand refused: %s", response.status_code)
            return None
        blocks = response.json().get("content") or []
        body = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
    except Exception:  # noqa: BLE001 - a search must never fail because this step did
        log.warning("understand unavailable", exc_info=True)
        return None
    read = _parse(body)
    # A refusal is not cached; a reading is, so the same query costs one call.
    _remember(folded, read)
    return read


def _apply(intent: IntentResponse, read: dict) -> IntentResponse:
    intent.need = read["need"]
    intent.eligibility_groups = [list(group) for group in read["must_include"]]
    # Every group must hit. The rules relax this for their long tail because they only have
    # the customer's raw words; the model returns the words that matter, so all of them do.
    intent.eligibility_min = None
    city = intent.location_city.value if isinstance(intent.location_city.value, str) else ""
    terms: list[str] = []
    for term in read["search_terms"]:
        for candidate in (term, f"{term} {city}".strip() if city else ""):
            if candidate and candidate not in terms:
                terms.append(candidate)
    intent.search_terms = terms[:MAX_TERMS * 2]
    if read["hiring"]:
        intent.result_unit = ResultUnit.SERVICE_PROVIDER
    elif intent.result_unit == ResultUnit.SERVICE_PROVIDER:
        # He is buying a thing; do not let the rules' hiring guess stand.
        intent.result_unit = ResultUnit.AD
    intent.understood = True
    return intent


def refine(query: str, intents: list[IntentResponse]) -> list[IntentResponse]:
    """Replace what the model read better, keep everything else the rules found."""
    read = read_query(query)
    if not read:
        return intents
    if len(read) == len(intents):
        return [_apply(intent, item) for intent, item in zip(intents, read)]
    # The model split the query differently. One need against one intent is still safe to
    # apply; any other disagreement is left to the rules, which know how to split.
    if len(read) == 1 and len(intents) == 1:
        return [_apply(intents[0], read[0])]
    return intents
