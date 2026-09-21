"""Arabic and mixed-script normalization. Folding is for matching, not for display."""

from __future__ import annotations

import re
import unicodedata

_DIACRITICS = re.compile(r"[\u064b-\u0652\u0670\u0640]")
_NON_WORD = re.compile(r"[^\w\s\u0600-\u06ff]+", re.UNICODE)
_SPACES = re.compile(r"\s+")

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
    value = unicodedata.normalize("NFKC", text).lower()
    value = _DIACRITICS.sub("", value)
    value = value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    value = value.replace("ى", "ي").replace("ة", "ه")
    value = value.replace("ؤ", "و").replace("ئ", "ي")
    value = _NON_WORD.sub(" ", value)
    value = _SPACES.sub(" ", value).strip()
    for source, target in _PHRASES:
        value = value.replace(source, target)
    return _SPACES.sub(" ", value).strip()


def tokens(text: str | None) -> list[str]:
    normalized = normalize(text)
    return normalized.split() if normalized else []


def contains_phrase(text: str | None, phrase: str) -> bool:
    haystack = f" {normalize(text)} "
    needle = f" {normalize(phrase)} "
    return bool(needle.strip()) and needle in haystack
