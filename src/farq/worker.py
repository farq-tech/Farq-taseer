"""Haraj router and sync.

Outgoing: queued deliveries from the item conversation go to each seller's Haraj
conversation, one at a time, from Taseer's own sending account only, at least
Outreach.min_spacing seconds plus a random jitter apart, and no more than
Outreach.daily_invites new seller threads per account a day (farq.outreach). An
invite opens with the seller's own listing title. Until HARAJ_TASEER_SEND_ENABLED=1
the account sends only the one delivery the owner approved as its canary
(CANARY_KEY). A refusal (401, 402, 403, 429, 451) stops sending for 30 minutes. A
message whose POST may have reached Haraj is never sent again. Incoming: seller replies are read from those
conversations only, at least 2 seconds apart, with 15 minutes of quiet after a
refusal. Pacing and pauses live in the store, so every instance (and every
serverless invocation) sees them.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from farq.push import notify_reply
from farq.store import QUOTE_LINK
from farq.haraj_chat import (
    READ_PAUSE_SECONDS,
    READ_SPACING_SECONDS,
    SEND_PAUSE_SECONDS,
    HarajChat,
    HarajChatUnavailable,
    HarajNotSent,
    HarajRefused,
    HarajSendUncertain,
    NotConnectedChat,
    conversation_account,
    with_reference,
)
from farq.outreach import Outreach, invite_seed, listing_title_for, personal_invite

MAX_ATTEMPTS = 5
# The one queued delivery the owner approved as a sending account's first message (a delivery id).
# Read only while the account is in canary mode; cleared as soon as that delivery is attempted.
CANARY_KEY = "send_canary_delivery_id"
log = logging.getLogger("farq.worker")


def media_label(media) -> str:
    kinds = {entry.get("type", "") for entry in media or ()}
    if any(kind.startswith("image/") for kind in kinds):
        return "📷 صورة"
    if "application/pdf" in kinds:
        return "📄 ملف"
    if "video/mp4" in kinds:
        return "🎬 فيديو"
    return "رسالة جديدة"


def public_base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "https://taseer.farq.sa").rstrip("/")

_started = False
_lock = threading.Lock()


def _paused(store, key: str, now: float) -> bool:
    value = store.get_value(key)
    return bool(value) and float(value) > now


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def dispatch_pending(
    store,
    chat: HarajChat,
    budget_seconds: float = 50,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    outreach: Outreach | None = None,
) -> int:
    if isinstance(chat, NotConnectedChat):
        return 0
    outreach = outreach or Outreach()
    # Which account sends, and whether it may: "open", "canary" (one approved delivery) or "closed".
    account = getattr(chat, "send_account_id", None)
    mode = getattr(chat, "send_mode", "open")
    if mode == "closed":
        return 0
    canary = None
    if mode == "canary":
        canary = store.get_value(CANARY_KEY)
        if not canary:
            return 0
    pause_key = f"send_paused_until:{account}" if account else "send_paused_until"
    sent = 0
    with _lock:
        deadline = clock() + budget_seconds
        while True:
            now = clock()
            if _paused(store, pause_key, now):
                break
            spacing = outreach.spacing()
            # One booking in the database paces every instance and every cron run together.
            slot = store.reserve_send_slot(spacing, now, deadline)
            if slot is None:
                break
            # A spent daily allowance stops new sellers, not the conversations already under way.
            continuing = None
            if account and canary is None and store.account_invites_since(account, _iso(now - 86400)) >= outreach.daily_invites:
                continuing = account
            claimed = store.claim_deliveries(limit=1, delivery_id=canary, continuing_on=continuing)
            if canary is not None:
                # One attempt per approval, whatever happens next.
                store.set_value(CANARY_KEY, None)
                log.warning("haraj canary %s from account %s: %s", canary, account, "claimed" if claimed else "not queued, nothing sent")
            if not claimed:
                store.release_send_slot(slot, spacing)
                break
            item = claimed[0]
            body = item["body"]
            if QUOTE_LINK in body:
                if not item.get("reply_token"):
                    store.release_send_slot(slot, spacing)
                    store.finish_delivery(item["id"], error="NO_QUOTE_LINK", retry=False)
                    continue
                body = body.replace(QUOTE_LINK, f"{public_base_url()}/s/{item['reply_token']}")
            # An invite opens with the seller's own listing, worded for him (farq.outreach).
            body = personal_invite(body, title=listing_title_for(item), seed=invite_seed(item["request_id"], item["seller_id"]))
            # The seller's one conversation with us is shared by every buyer: the reference tells his replies apart.
            body = with_reference(body, item.get("ref_code"))
            attachments = []
            for entry in item.get("media") or []:
                stored = store.get_file(entry["file_id"]) if entry.get("file_id") else None
                if stored is not None:
                    attachments.append(
                        {"content_type": stored["content_type"], "data": stored["data"], "name": stored["filename"], "width": stored["width"], "height": stored["height"]}
                    )
            # A thread that lives on another account's conversation opens a new one from this account.
            conversation = item["haraj_conversation_id"]
            if account and conversation and conversation_account(conversation, [account]) is None:
                conversation = None
            sleep(max(0.0, slot - clock()))
            try:
                result = chat.send(
                    conversation_id=conversation,
                    seller_id=item["seller_id"],
                    ad_id=item["ad_id"],
                    body=body,
                    attachments=attachments,
                )
            except HarajChatUnavailable as exc:
                store.release_send_slot(slot, spacing)
                store.finish_delivery(item["id"], error=exc.code, retry=True)
                break
            except HarajRefused as exc:
                retry = store.delivery_attempts(item["id"]) < MAX_ATTEMPTS
                store.finish_delivery(item["id"], error=f"REFUSED_{exc.status}", retry=retry)
                if exc.hard_stop:
                    store.set_value(pause_key, str(clock() + SEND_PAUSE_SECONDS))
                    break
                if canary is not None:
                    break
                continue
            except HarajSendUncertain as exc:
                store.finish_delivery(item["id"], error=exc.code, retry=False)
                if canary is not None:
                    break
                continue
            except (HarajNotSent, Exception) as exc:  # noqa: BLE001 - nothing was posted
                retry = store.delivery_attempts(item["id"]) < MAX_ATTEMPTS
                store.finish_delivery(item["id"], error=str(exc) or type(exc).__name__, retry=retry)
                if canary is not None:
                    break
                continue
            if account:
                store.finish_delivery(item["id"], sent=result, account_id=account)
            else:
                store.finish_delivery(item["id"], sent=result)
            sent += 1
            if canary is not None:
                break
    return sent


MEDIA_COPY_LIMIT = 10 * 1024 * 1024


def keep_media(store, request_id: str, media, fetch=None) -> tuple:
    """Haraj hands out media links that expire after a day; keep our own copy in the files table."""
    kept = []
    for entry in media or ():
        entry = dict(entry)
        url = entry.get("url") or ""
        if url.startswith("https://"):
            try:
                response = (fetch or _download)(url)
                if response is not None and len(response) <= MEDIA_COPY_LIMIT:
                    name = entry.get("name") or ("photo.jpg" if str(entry.get("type", "")).startswith("image/") else "file")
                    saved = store.save_file_for_request(request_id, entry["type"], name, response, entry.get("width"), entry.get("height"))
                    entry.update(url=saved["url"], file_id=saved["file_id"], size=saved["size"])
            except Exception:  # noqa: BLE001 - the Haraj link still works for a day
                pass
        kept.append(entry)
    return tuple(kept)


def _download(url: str) -> bytes | None:
    import httpx

    with httpx.stream("GET", url, timeout=20, follow_redirects=True) as response:
        if not response.is_success:
            return None
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MEDIA_COPY_LIMIT:
                return None
        return bytes(data)


# How soon a conversation is read again after a clean read. A request's sellers answer within
# hours, so a young request is read every run; an old one still gets a late reply within the hour.
FRESH_READ_SECONDS = 30
WEEK_READ_SECONDS = 10 * 60
OLD_READ_SECONDS = 60 * 60


def _age_seconds(created_at, now: float) -> float | None:
    if created_at is None:
        return None
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return now - created_at.timestamp()


def poll_interval(threads: list[dict], now: float) -> int:
    """Seconds until a conversation is due again, from its most recent activity.

    A conversation is shared by every request we sent that seller, so the youngest open request,
    or our latest message on any of them, sets the pace: a seller answering a fresh message is read
    promptly even on an old or closed request. Otherwise old and closed requests back off instead
    of crowding out new ones."""
    sends = [_age_seconds(item.get("last_sent_at"), now) for item in threads]
    if any(age is not None and age < 3 * 86400 for age in sends):
        return FRESH_READ_SECONDS
    open_ages = [_age_seconds(item.get("request_created_at"), now) for item in threads if not item.get("deal_outcome")]
    if not open_ages:
        return OLD_READ_SECONDS
    if any(age is None for age in open_ages):
        return FRESH_READ_SECONDS
    youngest = min(open_ages)
    if youngest < 3 * 86400:
        return FRESH_READ_SECONDS
    if youngest < 14 * 86400:
        return WEEK_READ_SECONDS
    return OLD_READ_SECONDS


def sync_replies(
    store,
    chat: HarajChat,
    budget_seconds: float = 40,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> int:
    """Read each supplier's Haraj conversation once, and file every reply under the right request."""
    if isinstance(chat, NotConnectedChat) or _paused(store, "inbox_paused_until", clock()):
        return 0
    # Each of our Haraj accounts is read with its own session; a refusal pauses that account only.
    accounts = list(getattr(chat, "readers", None) or [])
    received = 0
    deadline = clock() + budget_seconds
    synced: set[str] = set()
    conversations: dict[str, list[dict]] = {}
    for thread in store.threads_to_sync(now=clock()):
        conversations.setdefault(thread["haraj_conversation_id"], []).append(thread)
    for index, (conversation, threads) in enumerate(conversations.items()):
        if clock() + READ_SPACING_SECONDS > deadline:
            break
        account = conversation_account(conversation, accounts) if accounts else None
        account_pause = f"inbox_paused_until:{account}" if account else None
        if account_pause and _paused(store, account_pause, clock()):
            continue
        if index:
            sleep(READ_SPACING_SECONDS)
        seller_id = threads[0]["seller_id"]
        try:
            inbound = chat.fetch(
                conversation_id=conversation,
                seller_id=seller_id,
                after_seq=min(int(item.get("high_water") or 0) for item in threads),
            )
        except HarajRefused as exc:
            store.conversation_checked(conversation, f"HTTP_{exc.status}", READ_PAUSE_SECONDS if exc.hard_stop else 60, now=clock())
            if exc.hard_stop:
                store.set_value(account_pause or "inbox_paused_until", str(clock() + READ_PAUSE_SECONDS))
                if account_pause:
                    continue
            break
        except HarajChatUnavailable as exc:
            if exc.code in ("DISABLED", "CONFIGURATION_REQUIRED"):
                break
            # ACCOUNT_UNAVAILABLE: a conversation on an account this server no longer reads.
            skip = exc.code in ("AMBIGUOUS", "ACCOUNT_UNAVAILABLE")
            store.conversation_checked(conversation, exc.code, 3600 if skip else 60, now=clock())
            if skip:
                continue
            break
        highest = 0
        for item in inbound:
            highest = max(highest, item.seq)
            if store.has_haraj_message(item.haraj_message_id):
                continue
            thread = store.thread_for_inbound(conversation, item.sent_at, item.body)
            if thread is None:
                # No reference and open requests from several buyers: a guess could show one buyer another's reply.
                candidates = store.record_unmatched_inbound(conversation, seller_id, item)
                log.warning("haraj reply %s in %s matches no single request (candidates: %s); kept unmatched", item.haraj_message_id, conversation, ", ".join(candidates))
                continue
            if thread.get("routed_by"):
                log.warning(
                    "haraj reply %s in %s quotes no request reference; filed under the latest request we wrote to the seller about: %s (%s)",
                    item.haraj_message_id, conversation, thread["request_id"], thread["routed_by"],
                )
            if item.media:
                item = replace(item, media=keep_media(store, thread["request_id"], item.media))
            recorded = store.record_inbound(thread, item)
            if recorded is not None:
                received += 1
                synced.add(thread["request_id"])
                notify_reply(store, thread["request_id"], thread["seller_id"], item.body or media_label(item.media), message_id=recorded.id)
        store.conversation_checked(conversation, retry_seconds=poll_interval(threads, clock()), now=clock(), high_water=highest)
        synced.update(item["request_id"] for item in threads)
    for request_id in synced:
        store.mark_synced(request_id)
    return received


def poll_once(store, chat: HarajChat | None = None, **kwargs) -> tuple[int, int]:
    chat = chat or NotConnectedChat()
    return dispatch_pending(store, chat, **kwargs), sync_replies(store, chat, **kwargs)


def start_poller(store, chat: HarajChat | None = None, interval_seconds: int | None = None) -> None:
    """Long-running hosts only. On Vercel the cron route drives poll_once instead."""
    global _started
    if os.environ.get("FARQ_ENABLE_POLL", "1") != "1" or _started:
        return
    _started = True
    delay = interval_seconds if interval_seconds is not None else int(os.environ.get("FARQ_POLL_SECONDS", "60"))

    def loop() -> None:
        while True:
            try:
                poll_once(store, chat)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(max(30, delay))

    threading.Thread(target=loop, name="farq-haraj-sync", daemon=True).start()
