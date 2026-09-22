"""Haraj chat channel. Sellers only ever talk to us inside Haraj.

Tasseer shows the customer one conversation per item; every outgoing message is
routed to the Haraj conversation of each targeted seller, and seller replies are
pulled back by the sync worker. Haraj has no public messaging API, so the default
channel is not connected: deliveries stay queued and are never reported as sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


class HarajChatUnavailable(Exception):
    """The channel cannot reach Haraj right now. Deliveries stay queued."""


@dataclass(frozen=True)
class SentMessage:
    haraj_conversation_id: str
    haraj_message_id: str


@dataclass(frozen=True)
class InboundMessage:
    haraj_message_id: str
    body: str
    sent_at: str


class HarajChat(Protocol):
    def send(self, *, conversation_id: str | None, seller_id: str, ad_id: str | None, body: str) -> SentMessage:
        """Send into the seller's Haraj conversation, opening one from the ad when conversation_id is None."""

    def fetch(self, *, conversation_id: str, since: str | None) -> list[InboundMessage]:
        """Seller messages in this Haraj conversation newer than `since`, oldest first."""


class NotConnectedChat:
    def send(self, *, conversation_id: str | None, seller_id: str, ad_id: str | None, body: str) -> SentMessage:
        raise HarajChatUnavailable("haraj chat is not connected")

    def fetch(self, *, conversation_id: str, since: str | None) -> list[InboundMessage]:
        raise HarajChatUnavailable("haraj chat is not connected")


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٬", "01234567890123456789,")
_PRICE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:ر\.?\s?س|ريال|rs|sar)", re.IGNORECASE)


def extract_price(text: str) -> float | None:
    """A price only when the seller wrote a currency next to the number, e.g. "٢٥٠ ريال"."""
    found = _PRICE.search((text or "").translate(_DIGITS))
    if not found:
        return None
    value = float(found.group(1).replace(",", ""))
    return value if value > 0 else None
