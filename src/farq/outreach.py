"""How Taseer approaches a Haraj seller: who, how many, how often, and in what words.

Since 2026-09-28 the invites stopped being opened (about half opened before, 1 in 59 after) and
no seller answered. Taseer was writing from the Haraj account Farq Construction also writes from,
with one identical text for everyone, to as many as twenty sellers per request. Haraj treats that
as bulk messaging. The rules here keep every send looking like what it is, one buyer asking one
seller about one of his own listings:

- volume: at most ``max_invites_per_request`` sellers per request, at most ``daily_invites`` new
  seller conversations per sending account a day, and at least ``min_spacing`` seconds plus a
  random ``jitter`` between any two sends;
- targeting: a seller is invited only for a listing of his that a search showed this customer,
  whose title or category names the requested item (``listing_matches``);
- wording: the opener quotes the seller's own listing title and varies (``personal_invite``).
  It names no source and promises nothing beyond «a buyer is asking for this».
"""

from __future__ import annotations

import hashlib
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from farq.text import normalize


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except ValueError:
        value = default
    return max(minimum, value)


@dataclass(frozen=True)
class Outreach:
    max_invites_per_request: int = field(default_factory=lambda: _env_int("FARQ_MAX_INVITES_PER_REQUEST", 8, 1))
    daily_invites: int = field(default_factory=lambda: _env_int("FARQ_ACCOUNT_DAILY_INVITES", 60, 0))
    min_spacing: int = field(default_factory=lambda: _env_int("FARQ_SEND_MIN_SPACING_SECONDS", 45, 20))
    jitter: int = field(default_factory=lambda: _env_int("FARQ_SEND_JITTER_SECONDS", 30, 0))
    # Only listings that passed every eligibility rule; a «near» match is shown, never invited.
    allow_near: bool = field(default_factory=lambda: os.environ.get("FARQ_INVITE_NEAR_MATCHES", "0") == "1")

    def average_spacing(self) -> float:
        return self.min_spacing + self.jitter / 2

    def spacing(self, rng: random.Random | None = None) -> float:
        """Seconds until the next send may go out: never less than min_spacing, never regular."""
        return float(self.min_spacing) + (rng or random).uniform(0, self.jitter)


# -- Targeting -----------------------------------------------------------------------------

# Words that say nothing about what is wanted. «خدمات سباكة: سباك» is about «سباك».
_FILLER = {
    "في", "من", "على", "الى", "او", "و", "مع", "عن", "ل", "ب", "ابي", "ابغى", "ابغي", "احتاج", "اريد", "مطلوب",
    "مطلوبه", "ودي", "نبي", "عندي", "خدمات", "خدمه", "خدمة", "جديد", "جديده", "مستعمل", "مستعمله", "نظيف", "نظيفه",
    "للبيع", "بيع", "شراء", "سعر", "رخيص", "حي", "مدينه", "يصلح", "يركب", "تركيب", "صيانه", "اصلاح", "عدد", "قطعه",
    "حبه", "كبير", "صغير", "ممتاز",
}


def need_terms(need: str | None) -> list[str]:
    """The words of a request that name the thing: «أبواب PVC: ابواب pvc» -> [ابواب, pvc]."""
    from farq.cities import find_cities

    value = normalize(need)
    cities = {normalize(city) for city in find_cities(need or "")}
    seen: list[str] = []
    for word in value.split():
        if len(word) < 3 or word in _FILLER or word.isdigit() or any(word.endswith(city) for city in cities if city):
            continue
        if word not in seen:
            seen.append(word)
    return seen


def listing_matches(need: str | None, title: str | None, category_tags=()) -> str | None:
    """The word of the request the seller's listing names, or None when it names none of them.

    Search eligibility has already judged the listing against the full request; this is the floor
    every invite must clear on its own: a real listing title that names what the buyer asked for."""
    from farq.eligibility import contains_term

    if not title or not str(title).strip():
        return None
    text = " ".join([str(title), *[str(tag) for tag in category_tags or ()]])
    for term in need_terms(need):
        if contains_term(text, term):
            return term
    return None


def title_from_url(listing_url: str | None) -> str | None:
    """Haraj puts the listing title in the URL: ``/11188689785/معلمة_دروس_خصوصية/`` -> «معلمة دروس خصوصية»."""
    if not listing_url:
        return None
    try:
        parts = [part for part in urlsplit(listing_url).path.split("/") if part]
    except ValueError:
        return None
    if len(parts) < 2 or not parts[0].isdigit():
        return None
    title = unquote(parts[1]).replace("_", " ").strip()
    return title or None


def short_title(title: str | None, limit: int = 48) -> str | None:
    value = " ".join(str(title or "").replace("«", "").replace("»", "").split())
    if not value:
        return None
    if len(value) <= limit:
        return value
    cut = value[:limit].rsplit(" ", 1)[0]
    return (cut or value[:limit]) + "…"


def _recent(posted_at: str | None, now: datetime) -> float:
    try:
        moment = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return float("inf")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (now - moment).total_seconds())


def preference(listing: dict, city: str | None, now: datetime | None = None) -> tuple:
    """Sort key: sellers in the request's city first, then the most recently posted listing."""
    now = now or datetime.now(timezone.utc)
    same_city = bool(city) and normalize(listing.get("city")) == normalize(city)
    return (0 if same_city else 1, _recent(listing.get("posted_at"), now))


# -- Wording -------------------------------------------------------------------------------

OPENER = "{listing_opener}"
ASK = "{ask}"
CTA = "{cta}"
_WITH_TITLE = (
    "السلام عليكم، شفت إعلانك «{title}»",
    "هلا والله، شفت إعلانك «{title}»",
    "حيّاك الله، بخصوص إعلانك «{title}»",
)
_WITHOUT_TITLE = ("السلام عليكم", "هلا والله", "حيّاك الله")
_ASK = ("عندنا مشتري يطلب:", "فيه مشتري يدوّر على:", "عندنا طلب من مشتري على:")
_CTA = ("إذا متوفر عندك، أرسل سعرك من الرابط:", "متوفر عندك؟ قدّم عرضك من هنا:", "إذا يناسبك، حط سعرك من الرابط:")


def invite_template(item: str, city: str | None, quote_link: str) -> str:
    """The invite as stored on the message; the opener and wording are filled per seller when sent."""
    line = f"{item.strip()} في {city}" if city else item.strip()
    return "\n".join([OPENER, ASK, line, CTA, quote_link])


def _pick(options: tuple[str, ...], seed: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{seed}".encode()).digest()
    return options[digest[0] % len(options)]


def personal_invite(body: str, *, title: str | None, seed: str) -> str:
    """Fill a stored invite for one seller. ``seed`` (request and seller) keeps the choice stable
    across retries; different sellers get different phrasings. A text with no opener slot (an
    invite queued before this change, or any other message) comes back unchanged."""
    if OPENER not in (body or ""):
        return body
    shown = short_title(title)
    opener = _pick(_WITH_TITLE, seed, "open").format(title=shown) if shown else _pick(_WITHOUT_TITLE, seed, "open")
    # The wording first, the seller's own title last: a title can never fill a slot.
    return body.replace(ASK, _pick(_ASK, seed, "ask")).replace(CTA, _pick(_CTA, seed, "cta")).replace(OPENER, opener)


def readable_invite(body: str) -> str:
    """The stored invite as a person reads it (staff screens, the seller's own page): neutral wording."""
    return personal_invite(body, title=None, seed="view") if OPENER in (body or "") else body


def invite_seed(request_id: str | None, seller_id: str | None) -> str:
    """The same seller on the same request always reads the same wording."""
    from farq.haraj_chat import author_id

    return f"{request_id or ''}:{author_id(seller_id) or str(seller_id or '').strip()}"


def listing_title_for(recipient) -> str | None:
    """The title of the listing a recipient was invited for: as recorded, else read off its URL."""
    title = getattr(recipient, "listing_title", None) if not isinstance(recipient, dict) else recipient.get("listing_title")
    url = getattr(recipient, "listing_url", None) if not isinstance(recipient, dict) else recipient.get("listing_url")
    return (str(title).strip() or None) if title else title_from_url(url)
