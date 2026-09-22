"""Haraj router and sync.

Outgoing: queued deliveries from the item conversation go to each seller's Haraj
conversation. Incoming: seller replies are pulled from those conversations and
attached to their item and seller. Nothing is marked sent unless Haraj accepted it,
and last_synced_at only moves when Haraj was actually read.
"""

from __future__ import annotations

import os
import threading
import time

from farq.haraj_chat import HarajChat, HarajChatUnavailable, NotConnectedChat
from farq.store import Store

MAX_ATTEMPTS = 5

_started = False
_lock = threading.Lock()


def dispatch_pending(store: Store, chat: HarajChat) -> int:
    sent = 0
    with _lock:
        for item in store.claim_deliveries():
            try:
                result = chat.send(
                    conversation_id=item["haraj_conversation_id"],
                    seller_id=item["seller_id"],
                    ad_id=item["ad_id"],
                    body=item["body"],
                )
            except HarajChatUnavailable as exc:
                store.finish_delivery(item["id"], error=str(exc), retry=True)
                continue
            except Exception as exc:  # noqa: BLE001 - one seller failing must not stop the others
                attempts = store.delivery_attempts(item["id"])
                store.finish_delivery(item["id"], error=str(exc), retry=attempts < MAX_ATTEMPTS)
                continue
            store.finish_delivery(item["id"], sent=result)
            sent += 1
    return sent


def sync_replies(store: Store, chat: HarajChat) -> int:
    received = 0
    synced: set[str] = set()
    for thread in store.threads_to_sync():
        try:
            inbound = chat.fetch(conversation_id=thread["haraj_conversation_id"], since=thread["last_fetched_at"])
        except HarajChatUnavailable:
            return received
        except Exception:  # noqa: BLE001
            continue
        for item in inbound:
            if store.record_inbound(thread, item) is not None:
                received += 1
        synced.add(thread["request_id"])
    for request_id in synced:
        store.mark_synced(request_id)
    return received


def poll_once(store: Store, chat: HarajChat | None = None) -> tuple[int, int]:
    chat = chat or NotConnectedChat()
    return dispatch_pending(store, chat), sync_replies(store, chat)


def start_poller(store: Store, chat: HarajChat | None = None, interval_seconds: int | None = None) -> None:
    global _started
    if os.environ.get("FARQ_ENABLE_POLL", "1") != "1":
        return
    if _started:
        return
    _started = True
    delay = interval_seconds if interval_seconds is not None else int(os.environ.get("FARQ_POLL_SECONDS", "120"))

    def loop() -> None:
        while True:
            try:
                poll_once(store, chat)
            except Exception:
                pass
            time.sleep(max(30, delay))

    threading.Thread(target=loop, name="farq-haraj-sync", daemon=True).start()
