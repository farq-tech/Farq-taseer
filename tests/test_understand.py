"""The model step: it replaces what it read well, and never breaks a search when it cannot."""

import farq.understand as understand
from farq.contracts import ResultUnit
from farq.intent import analyze, analyze_needs


def clear_cache():
    understand._cache.clear()


def test_without_a_key_the_rules_run_alone(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    clear_cache()
    assert understand.enabled() is False
    intents = analyze_needs("ابي واحد يركب لي كاميرات مراقبة")
    assert understand.refine("ابي واحد يركب لي كاميرات مراقبة", intents) is intents


def test_the_switch_turns_it_off_even_with_a_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("FARQ_UNDERSTAND", "0")
    clear_cache()
    assert understand.enabled() is False


def _reply(body: str):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"content": [{"type": "text", "text": body}]}

    return Response()


def _use(monkeypatch, body, *, status=200):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("FARQ_UNDERSTAND", raising=False)
    clear_cache()

    def post(*args, **kwargs):
        response = _reply(body)
        if status != 200:
            class Refused:
                status_code = status
            return Refused()
        return response

    monkeypatch.setattr(understand.httpx, "post", post)


GOOD = """{"needs": [{"need": "كاميرات مراقبة", "hiring": true,
  "must_include": [["كاميرا", "كاميرات"], ["مراقبه", "cctv"]],
  "search_terms": ["تركيب كاميرات مراقبة", "فني كاميرات مراقبة"]}]}"""


def test_a_good_reading_replaces_the_need_the_words_and_the_queries(monkeypatch):
    _use(monkeypatch, GOOD)
    query = "ابي واحد يركب لي كاميرات مراقبة بالرياض"
    refined = understand.refine(query, analyze_needs(query))[0]
    assert refined.need == "كاميرات مراقبة"
    assert refined.result_unit == ResultUnit.SERVICE_PROVIDER
    assert ["كاميرا", "كاميرات"] in refined.eligibility_groups
    assert "تركيب كاميرات مراقبة" in refined.search_terms
    # The city the rules found is kept and appended to the model's phrases.
    assert any("الرياض" in term for term in refined.search_terms)
    # Every group must hit: the long tail's two-thirds rule does not apply to read words.
    assert refined.eligibility_min is None


def test_buying_is_not_turned_into_hiring(monkeypatch):
    _use(monkeypatch, GOOD.replace('"hiring": true', '"hiring": false'))
    query = "ابي واحد يركب لي كاميرات مراقبة"
    refined = understand.refine(query, analyze_needs(query))[0]
    assert refined.result_unit != ResultUnit.SERVICE_PROVIDER


def test_the_reading_is_cached_so_one_query_costs_one_call(monkeypatch):
    _use(monkeypatch, GOOD)
    calls = []
    original = understand.httpx.post
    monkeypatch.setattr(understand.httpx, "post", lambda *a, **k: (calls.append(1), original(*a, **k))[1])
    understand.read_query("ابي كاميرات مراقبة")
    understand.read_query("ابي كاميرات مراقبة")
    assert len(calls) == 1


BAD_BODIES = [
    "ليس JSON إطلاقًا",
    '{"needs": []}',
    '{"needs": [{"need": "كاميرات"}]}',                      # no words, no queries
    '{"needs": [{"need": "", "must_include": [["ك"]], "search_terms": ["ك"]}]}',
    '{"needs": [{"need": "كاميرات", "must_include": "نص", "search_terms": ["ك"]}]}',
    '{"needs": "كاميرات"}',
]


def test_a_bad_answer_leaves_the_rules_untouched(monkeypatch):
    query = "ابي درابزين ستانلس للدرج"
    for body in BAD_BODIES:
        _use(monkeypatch, body)
        intents = analyze_needs(query)
        before = (intents[0].need, intents[0].result_unit, list(intents[0].search_terms))
        refined = understand.refine(query, intents)[0]
        assert (refined.need, refined.result_unit, list(refined.search_terms)) == before, body


def test_a_refusal_or_a_crash_leaves_the_rules_untouched(monkeypatch):
    query = "ابي درابزين ستانلس"
    _use(monkeypatch, GOOD, status=429)
    assert understand.read_query(query) is None

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    clear_cache()

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(understand.httpx, "post", boom)
    assert understand.read_query(query) is None
    intents = analyze_needs(query)
    assert understand.refine(query, intents) is intents


def test_a_different_split_is_left_to_the_rules(monkeypatch):
    two = """{"needs": [
      {"need": "سباك", "hiring": true, "must_include": [["سباك"]], "search_terms": ["سباك"]},
      {"need": "كهربائي", "hiring": true, "must_include": [["كهربائي"]], "search_terms": ["كهربائي"]},
      {"need": "نجار", "hiring": true, "must_include": [["نجار"]], "search_terms": ["نجار"]}]}"""
    _use(monkeypatch, two)
    query = "ابي درابزين ستانلس"
    intents = analyze_needs(query)
    assert len(intents) == 1
    assert understand.refine(query, intents) is intents


def test_a_reading_is_shared_between_instances_through_the_store(monkeypatch):
    """Two processes (M02 on one, the search on another) share the store, not memory."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"content": [{"type": "text", "text": '{"needs": [{"need": "صدام كامري", "hiring": false, "must_include": [["صدام"], ["كامري"]], "search_terms": ["صدام كامري 2019"]}]}'}]}

    def fake_post(*args, **kwargs):
        calls.append(1)
        return Response()

    class Kv:
        def __init__(self):
            self.rows = {}

        def get_value(self, key):
            return self.rows.get(key)

        def set_value(self, key, value):
            self.rows[key] = value

    kv = Kv()
    monkeypatch.setattr(understand.httpx, "post", fake_post)
    understand._cache.clear()
    understand.use_cache(kv)
    try:
        first = understand.read_query("أبي صدام كامري 2019")
        assert first and first[0]["need"] == "صدام كامري" and len(calls) == 1
        assert len(kv.rows) == 1
        # Another instance: empty memory, same store.
        understand._cache.clear()
        second = understand.read_query("ابي صدام كامري 2019")
        assert second == first and len(calls) == 1
    finally:
        understand.use_cache(None)
        understand._cache.clear()
