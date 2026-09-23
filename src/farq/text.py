"""Arabic and mixed-script normalization. Folding is for matching, not for display."""

from __future__ import annotations

import re
import unicodedata

_DIACRITICS = re.compile(r"[\u064b-\u0652\u0670\u0640]")
_NON_WORD = re.compile(r"[^\w\s\u0600-\u06ff]+", re.UNICODE)
_SPACES = re.compile(r"\s+")
# Arabic comma, semicolon, question mark, percent and full stop sit inside the
# Arabic block, so _NON_WORD keeps them glued to the word ("سباك،").
_AR_PUNCT = re.compile(r"[\u060c\u061b\u061f\u066a-\u066d\u06d4]")
# Arabic-Indic and Persian digits. NFKC leaves them alone, so "٢٠٢٤" was not a year.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_PHRASES = (
    ("بلاي ستيشن", "بلايستيشن"),
    ("بلي ستيشن", "بلايستيشن"),
    ("بلاي ستيشن", "بلايستيشن"),
    ("ستانلس ستيل", "ستانلس"),
    ("ستان ستيل", "ستانلس"),
    ("استانلس ستيل", "ستانلس"),
    ("ستينلس ستيل", "ستانلس"),
    ("stainless steel", "ستانلس"),
    ("stainless", "ستانلس"),
    ("استانلس", "ستانلس"),
    ("ستان لس", "ستانلس"),
    ("استيل", "ستانلس"),
    ("دربزين", "درابزين"),
    ("دربيزين", "درابزين"),
    ("استنلس", "ستانلس"),
    ("نستيل", "ستانلس"),
    ("آيفون", "ايفون"),
    ("أيفون", "ايفون"),
    ("ايفون", "ايفون"),
    ("بلايستيشن٥", "بلايستيشن 5"),
    ("بلايستيشن5", "بلايستيشن 5"),
)


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text).lower().translate(_DIGITS)
    value = _DIACRITICS.sub("", value)
    value = value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    value = value.replace("ى", "ي").replace("ة", "ه")
    value = value.replace("ؤ", "و").replace("ئ", "ي")
    value = _AR_PUNCT.sub(" ", value)
    value = _NON_WORD.sub(" ", value)
    value = _SPACES.sub(" ", value).strip()
    for source, target in _PHRASES:
        value = value.replace(source, target)
    return _SPACES.sub(" ", value).strip()


def to_ascii_digits(text: str | None) -> str:
    return (text or "").translate(_DIGITS)


# Attached conjunction, then preposition, then the article: و/ف + ب/ل/ك + ال.
_CONJ = ("", "و", "ف")
_PREP = ("", "ب", "ل", "ك")


def prefix_variants(token: str) -> list[str]:
    """The token plus every reading with an attached و/ف, ب/ل/ك and ال removed.

    "لنقل" -> "نقل", "والعفش" -> "عفش", "للدمام" -> "الدمام" and "دمام". A
    stripped form must keep at least three letters, so short words are not
    turned into other words.
    """

    forms = [token]

    def add(value: str) -> None:
        if len(value) >= 3 and value not in forms:
            forms.append(value)

    for conj in _CONJ:
        if conj and not token.startswith(conj):
            continue
        rest = token[len(conj):]
        if conj:
            add(rest)
        for prep in _PREP:
            if prep and not rest.startswith(prep):
                continue
            body = rest[len(prep):]
            if prep:
                add(body)
            if prep == "ل" and body.startswith("ل"):
                # "للدمام" is ل + "الدمام" with the alif dropped.
                add("ا" + body)
                add(body[1:])
            if body.startswith("ال"):
                add(body[2:])
    return forms


def tokens(text: str | None) -> list[str]:
    normalized = normalize(text)
    return normalized.split() if normalized else []


def contains_phrase(text: str | None, phrase: str) -> bool:
    haystack = f" {normalize(text)} "
    needle = f" {normalize(phrase)} "
    return bool(needle.strip()) and needle in haystack
