"""Who a quote request goes to on Haraj, and the first words he reads.

Taseer writes to sellers from one Haraj account. Haraj hides an account that sends the
same text to hundreds of people, and a seller ignores a message about something he does
not sell. So, before anything is queued:

- only a seller whose own listing matches the item is written to: a listing the
  customer's search showed (search_listings), passing every eligibility rule (an
  "exact" match, never a "near" one), whose TITLE or category names the item;
- a generic personal account (Haraj's default «عضو 12 3456», «anonymous…», a name that is
  a phrase of dhikr) is written to only with a recent listing in the request's city,
  and always after the named sellers;
- the same seller is written to once per item, however many of his ads were picked;
- at most ``max_invites`` sellers per request, the best first: in the request's city,
  then recently active, then the rest.

The first message names the seller's own listing, varies its wording a little, names no
source, and keeps the request's reference so his reply finds its request.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from farq.config import _env_bool, _env_int
from farq.eligibility import _group_hit
from farq.text import normalize

# -- targeting ------------------------------------------------------------------------


@dataclass(frozen=True)
class Targeting:
    max_invites: int = field(default_factory=lambda: _env_int("FARQ_MAX_INVITES_PER_REQUEST", 8))
    # On in production. Off only where no search is recorded (the legacy test journeys).
    require_listing_match: bool = field(default_factory=lambda: _env_bool("FARQ_REQUIRE_LISTING_MATCH", True))
    recent_days: int = field(default_factory=lambda: _env_int("FARQ_INVITE_RECENT_DAYS", 30))


_GENERIC_NAME = re.compile(r"^(?:عضو[\s\d]*|anonymous\w*|user[\s_]*\d+|[\d\s]+)$", re.IGNORECASE)
_DHIKR = ("لا اله الا الله", "سبحان الله", "استغفر الله", "الحمد لله", "لا حول ولا قوه", "اللهم", "باسمك ربي", "بسم الله", "توكلت على الله", "ما شاء الله")


def generic_account(name: str | None) -> bool:
    """A Haraj account that never chose a trading name: the default «عضو …», an anonymous
    handle, bare digits, or a line of dhikr. Personal, not a shop."""
    value = (name or "").strip()
    if not value:
        return True
    if _GENERIC_NAME.match(value):
        return True
    folded = normalize(value)
    return any(phrase in folded for phrase in _DHIKR)


def listing_evidence(intent, results) -> list[dict]:
    """What a search proves about each listing it showed, for the targeting gate.
    title_match: an item word group (or, without groups, a search term) is in the ad's
    title or category tags, not only in its body or the seller's name."""
    groups = [list(group) for group in (getattr(intent, "eligibility_groups", None) or [])]
    if not groups:
        groups = [[term] for term in (getattr(intent, "search_terms", None) or []) if term]
    need = getattr(intent, "need", None)
    rows = []
    for result in results or ():
        ad = result.ad
        seller = result.seller or (ad.seller if ad is not None else None)
        if ad is None or not ad.id or seller is None or not seller.id:
            continue
        title_text = " ".join([ad.title or "", *(ad.category_tags or [])])
        rows.append(
            {
                "seller_id": seller.id,
                "seller_name": seller.name,
                "ad_id": str(ad.id),
                "ad_title": ad.title,
                "ad_city": ad.city or seller.city,
                "posted_at": ad.posted_at,
                "listing_state": ad.listing_state,
                "match": result.match,
                "title_match": bool(groups) and any(_group_hit(title_text, group) for group in groups),
                "need": need,
            }
        )
    return rows


def _moment(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _key(seller_id: str) -> str:
    from farq.store import seller_key

    return seller_key(seller_id)


@dataclass
class Selection:
    kept: list = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


def select_recipients(recipients, listings: list[dict], city: str | None, default_need: str | None, config: Targeting | None = None, now: datetime | None = None) -> Selection:
    """The recipients worth writing to, best first, at most config.max_invites, each with
    the listing (ad_id, ad_title) the opener will name. ``recipients`` are RequestRecipient
    objects; ``listings`` are listing_evidence rows from this customer's searches."""
    config = config or Targeting()
    now = now or datetime.now(timezone.utc)
    by_ad: dict[tuple[str, str], dict] = {}
    by_seller: dict[str, list[dict]] = {}
    for row in listings:
        key = _key(row["seller_id"])
        by_ad[(key, str(row["ad_id"]))] = row
        by_seller.setdefault(key, []).append(row)

    def usable(row: dict | None) -> bool:
        return row is not None and row.get("match", "exact") == "exact" and bool(row.get("title_match")) and row.get("listing_state") not in ("deleted", "stale")

    def evidence_for(item) -> dict | None:
        key = _key(item.seller_id)
        named = by_ad.get((key, str(item.ad_id))) if item.ad_id else None
        if usable(named):
            return named
        others = [row for row in by_seller.get(key, []) if usable(row)]
        if not others:
            return named
        return max(others, key=lambda row: _moment(row.get("posted_at")) or datetime.min.replace(tzinfo=timezone.utc))

    selection = Selection()
    candidates: dict[tuple[str, str], tuple[tuple, object, dict | None]] = {}
    for item in recipients:
        need = item.need or default_need or ""
        evidence = evidence_for(item)
        reason = None
        posted = _moment(evidence.get("posted_at")) if evidence else None
        recent = posted is not None and (now - posted).days <= config.recent_days
        in_city = bool(evidence and city and evidence.get("ad_city") == city)
        generic = generic_account(item.seller_name)
        if config.require_listing_match:
            if evidence is None:
                reason = "no_listing"
            elif evidence.get("match", "exact") != "exact":
                reason = "near_match"
            elif not evidence.get("title_match"):
                reason = "listing_not_matching"
            elif evidence.get("listing_state") in ("deleted", "stale"):
                reason = "listing_not_active"
            elif generic and not (in_city and recent):
                reason = "generic_account"
        if reason is not None:
            selection.skipped.append({"seller_id": item.seller_id, "seller_name": item.seller_name, "need": need or None, "reason": reason})
            continue
        rank = (in_city, recent, not generic, (posted or datetime.min.replace(tzinfo=timezone.utc)).timestamp())
        if evidence is not None:
            item = item.model_copy(update={"ad_id": str(evidence["ad_id"]), "ad_title": evidence.get("ad_title")})
        slot = (_key(item.seller_id), need)
        if slot in candidates:
            # One invite per seller per item, whichever of his ads were picked.
            if rank <= candidates[slot][0]:
                selection.skipped.append({"seller_id": item.seller_id, "seller_name": item.seller_name, "need": need or None, "reason": "duplicate_seller"})
                continue
            previous = candidates[slot][1]
            selection.skipped.append({"seller_id": previous.seller_id, "seller_name": previous.seller_name, "need": need or None, "reason": "duplicate_seller"})
        candidates[slot] = (rank, item, evidence)

    # Best first within each item, then items take turns so one item cannot use the whole cap.
    per_need: dict[str, list] = {}
    for (_seller, need), (rank, item, _evidence) in candidates.items():
        per_need.setdefault(need, []).append((rank, item))
    queues = [[item for _rank, item in sorted(rows, key=lambda pair: pair[0], reverse=True)] for rows in per_need.values()]
    ordered = []
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    cap = max(1, config.max_invites)
    selection.kept = ordered[:cap]
    for item in ordered[cap:]:
        selection.skipped.append({"seller_id": item.seller_id, "seller_name": item.seller_name, "need": item.need or default_need or None, "reason": "over_invite_cap"})
    return selection


# -- the first message ----------------------------------------------------------------

# Stored in messages.haraj_text where the opener goes; filled per seller when sending.
LISTING_OPENER = "{listing_opener}"
ASK_LEAD = "عندنا عميل يبي:"
LINK_LEAD = "إذا متوفر عندك، حط سعرك من هنا:"

_OPENERS_WITH_TITLE = (
    "السلام عليكم، شفت إعلانك «{title}»",
    "هلا والله، شفت إعلانك «{title}»",
    "حياك الله، شفت إعلانك «{title}»",
    "السلام عليكم ورحمة الله، لفت نظري إعلانك «{title}»",
)
_OPENERS = ("السلام عليكم", "هلا والله", "حياك الله", "السلام عليكم ورحمة الله")
_ASKS = (ASK_LEAD, "معنا عميل يدوّر:", "فيه عميل يطلب:")
_LINKS = (LINK_LEAD, "لو متوفر، أرسل سعرك من الرابط:", "إذا يناسبك، قدّم عرضك من هنا:")

TITLE_CHARS = 45
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_PHONE = re.compile(r"\+?\d[\d\s-]{7,}\d")
_UNSAFE = re.compile(r"[«»\"'`{}<>\[\]\n\r\t#*_|]+")


def short_title(title: str | None) -> str | None:
    """The ad title as a seller would recognise it: no links, phone numbers or quotes,
    one line, cut at a word under TITLE_CHARS characters."""
    value = _URL.sub(" ", title or "")
    value = _PHONE.sub(" ", value)
    value = _UNSAFE.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip(" -–—،,.")
    if len(value) < 2:
        return None
    if len(value) <= TITLE_CHARS:
        return value
    cut = value[:TITLE_CHARS].rsplit(" ", 1)[0].strip(" -–—،,.")
    return (cut or value[:TITLE_CHARS]) + "…"


def invite_text(item: str, city: str | None, quote_link: str) -> str:
    """The invite as stored: the item and the city only (no quantities, prices, notes or
    buyer number), with the opener left for render_invite to fill per seller."""
    line = f"{item.strip()} في {city}" if city else item.strip()
    return "\n".join([LISTING_OPENER, f"{ASK_LEAD} {line}", LINK_LEAD, quote_link])


def _pick(options: tuple, seed: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{seed}".encode()).digest()
    return options[digest[0] % len(options)]


def render_invite(text: str, ad_title: str | None = None, seed: str = "") -> str:
    """The invite one seller receives: his listing named, the wording picked by seed (stable
    for one delivery, different across sellers). Any other text is returned unchanged."""
    if LISTING_OPENER not in (text or ""):
        return text
    title = short_title(ad_title)
    opener = _pick(_OPENERS_WITH_TITLE, seed, "opener").format(title=title) if title else _pick(_OPENERS, seed, "opener")
    lines = []
    for line in text.split("\n"):
        if line == LISTING_OPENER:
            lines.append(opener)
        elif line.startswith(ASK_LEAD):
            lines.append(_pick(_ASKS, seed, "ask") + line[len(ASK_LEAD):])
        elif line == LINK_LEAD:
            lines.append(_pick(_LINKS, seed, "link"))
        else:
            lines.append(line)
    return "\n".join(lines)


def daily_send_cap() -> int:
    return _env_int("HARAJ_DAILY_SEND_CAP", 60)


def send_spacing_seconds() -> float:
    return float(_env_int("HARAJ_SEND_SPACING_SECONDS", 45))


def send_jitter_seconds() -> float:
    return float(_env_int("HARAJ_SEND_JITTER_SECONDS", 45))

