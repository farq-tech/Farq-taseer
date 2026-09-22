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

import os
import threading
import time
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
)

MAX_ATTEMPTS = 5


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


def sync_replies(
    store,
    chat: HarajChat,
    budget_seconds: float = 40,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> int:
    if isinstance(chat, NotConnectedChat) or _paused(store, "inbox_paused_until", clock()):
        return 0
    received = 0
    deadline = clock() + budget_seconds
    synced: set[str] = set()
    for index, thread in enumerate(store.threads_to_sync(now=clock())):
        if clock() + READ_SPACING_SECONDS > deadline:
            break
        if index:
            sleep(READ_SPACING_SECONDS)
        try:
            inbound = chat.fetch(
                conversation_id=thread["haraj_conversation_id"],
                seller_id=thread["seller_id"],
                after_seq=int(thread.get("high_water") or 0),
            )
        except HarajRefused as exc:
            store.thread_checked(thread, f"HTTP_{exc.status}", READ_PAUSE_SECONDS if exc.hard_stop else 60, now=clock())
            if exc.hard_stop:
                store.set_value("inbox_paused_until", str(clock() + READ_PAUSE_SECONDS))
            break
        except HarajChatUnavailable as exc:
            if exc.code in ("DISABLED", "CONFIGURATION_REQUIRED"):
                break
            store.thread_checked(thread, exc.code, 3600 if exc.code == "AMBIGUOUS" else 60, now=clock())
            if exc.code == "AMBIGUOUS":
                continue
            break
        for item in inbound:
            if store.record_inbound(thread, item) is not None:
                received += 1
                notify_reply(store, thread["request_id"], thread["seller_id"], item.body or media_label(item.media))
        store.thread_checked(thread, now=clock())
        synced.add(thread["request_id"])
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
