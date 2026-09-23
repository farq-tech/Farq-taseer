"""Haraj router and sync.

Outgoing: queued deliveries from the item conversation go to each seller's Haraj
conversation, one at a time, at least 20 seconds apart. A refusal (401, 402, 403,
429, 451) stops sending for 30 minutes. A message whose POST may have reached
Haraj is never sent again. Incoming: seller replies are read from those
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
from typing import Callable

from farq.push import notify_reply
from farq.store import QUOTE_LINK
from farq.haraj_chat import (
    READ_PAUSE_SECONDS,
    READ_SPACING_SECONDS,
    SEND_PAUSE_SECONDS,
    SEND_SPACING_SECONDS,
    HarajChat,
    HarajChatUnavailable,
    HarajNotSent,
    HarajRefused,
    HarajSendUncertain,
    NotConnectedChat,
    with_reference,
)

MAX_ATTEMPTS = 5
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


def dispatch_pending(
    store,
    chat: HarajChat,
    budget_seconds: float = 50,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> int:
    if isinstance(chat, NotConnectedChat):
        return 0
    sent = 0
    with _lock:
        deadline = clock() + budget_seconds
        while True:
            now = clock()
            if _paused(store, "send_paused_until", now):
                break
            # One booking in the database paces every instance and every cron run together.
            slot = store.reserve_send_slot(SEND_SPACING_SECONDS, now, deadline)
            if slot is None:
                break
            claimed = store.claim_deliveries(limit=1)
            if not claimed:
                store.release_send_slot(slot, SEND_SPACING_SECONDS)
                break
            item = claimed[0]
            body = item["body"]
            if QUOTE_LINK in body:
                if not item.get("reply_token"):
                    store.release_send_slot(slot, SEND_SPACING_SECONDS)
                    store.finish_delivery(item["id"], error="NO_QUOTE_LINK", retry=False)
                    continue
                body = body.replace(QUOTE_LINK, f"{public_base_url()}/s/{item['reply_token']}")
            # The seller's one conversation with us is shared by every buyer: the reference tells his replies apart.
            body = with_reference(body, item.get("ref_code"))
            attachments = []
            for entry in item.get("media") or []:
                stored = store.get_file(entry["file_id"]) if entry.get("file_id") else None
                if stored is not None:
                    attachments.append(
                        {"content_type": stored["content_type"], "data": stored["data"], "name": stored["filename"], "width": stored["width"], "height": stored["height"]}
                    )
            sleep(max(0.0, slot - clock()))
            try:
                result = chat.send(
                    conversation_id=item["haraj_conversation_id"],
                    seller_id=item["seller_id"],
                    ad_id=item["ad_id"],
                    body=body,
                    attachments=attachments,
                )
            except HarajChatUnavailable as exc:
                store.release_send_slot(slot, SEND_SPACING_SECONDS)
                store.finish_delivery(item["id"], error=exc.code, retry=True)
                break
            except HarajRefused as exc:
                retry = store.delivery_attempts(item["id"]) < MAX_ATTEMPTS
                store.finish_delivery(item["id"], error=f"REFUSED_{exc.status}", retry=retry)
                if exc.hard_stop:
                    store.set_value("send_paused_until", str(clock() + SEND_PAUSE_SECONDS))
                    break
                continue
            except HarajSendUncertain as exc:
                store.finish_delivery(item["id"], error=exc.code, retry=False)
                continue
            except (HarajNotSent, Exception) as exc:  # noqa: BLE001 - nothing was posted
                retry = store.delivery_attempts(item["id"]) < MAX_ATTEMPTS
                store.finish_delivery(item["id"], error=str(exc) or type(exc).__name__, retry=retry)
                continue
            store.finish_delivery(item["id"], sent=result)
            sent += 1
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
    received = 0
    deadline = clock() + budget_seconds
    synced: set[str] = set()
    conversations: dict[str, list[dict]] = {}
    for thread in store.threads_to_sync(now=clock()):
        conversations.setdefault(thread["haraj_conversation_id"], []).append(thread)
    for index, (conversation, threads) in enumerate(conversations.items()):
        if clock() + READ_SPACING_SECONDS > deadline:
            break
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
                store.set_value("inbox_paused_until", str(clock() + READ_PAUSE_SECONDS))
            break
        except HarajChatUnavailable as exc:
            if exc.code in ("DISABLED", "CONFIGURATION_REQUIRED"):
                break
            store.conversation_checked(conversation, exc.code, 3600 if exc.code == "AMBIGUOUS" else 60, now=clock())
            if exc.code == "AMBIGUOUS":
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
            if item.media:
                item = replace(item, media=keep_media(store, thread["request_id"], item.media))
            if store.record_inbound(thread, item) is not None:
                received += 1
                synced.add(thread["request_id"])
                notify_reply(store, thread["request_id"], thread["seller_id"], item.body or media_label(item.media))
        store.conversation_checked(conversation, now=clock(), high_water=highest)
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
