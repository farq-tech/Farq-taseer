"""Versioned contracts for the consumer journey. Unknown values stay null."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_serializer


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


CONTRACT_VERSION = "1"


class SearchState(str, Enum):
    RESULTS = "RESULTS"
    NOT_UNDERSTOOD = "NOT_UNDERSTOOD"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    LOCAL_EMPTY = "LOCAL_EMPTY"
    LIVE_SEARCHING = "LIVE_SEARCHING"
    LIVE_EMPTY = "LIVE_EMPTY"
    LIVE_UNAVAILABLE = "LIVE_UNAVAILABLE"
    PARTIAL_RESULTS = "PARTIAL_RESULTS"
    STALE_AD = "STALE_AD"
    DELETED_AD = "DELETED_AD"
    SELLER_UNAVAILABLE = "SELLER_UNAVAILABLE"
    LOCATION_AMBIGUOUS = "LOCATION_AMBIGUOUS"
    TIMEOUT = "TIMEOUT"
    NO_QUALIFIED_RESULTS = "NO_QUALIFIED_RESULTS"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ResultUnit(str, Enum):
    AD = "ad"
    SELLER = "seller"
    SERVICE_PROVIDER = "service_provider"
    HYBRID = "hybrid"


class LocationSensitivity(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    IRRELEVANT = "irrelevant"


class FieldValue(ContractModel):
    value: Any = None
    confidence: float = 0
    evidence: str | None = None

    @property
    def known(self) -> bool:
        return self.value is not None and self.confidence > 0


def unknown() -> FieldValue:
    return FieldValue(value=None, confidence=0, evidence=None)


def known(value: Any, confidence: float, evidence: str) -> FieldValue:
    return FieldValue(value=value, confidence=confidence, evidence=evidence)


class IntentResponse(ContractModel):
    contract_version: str = CONTRACT_VERSION
    original_query: str
    need: str | None = None
    # This need's own words as the customer typed them (unfolded spelling, with
    # the leading "ابي/ابغى" dropped and the shared city appended when the city
    # was said once for several needs). Searching this text again keeps the
    # material, the job and the quantity that the short `need` label leaves out.
    segment_text: str | None = None
    type: FieldValue = Field(default_factory=unknown)
    category: FieldValue = Field(default_factory=unknown)
    subcategory: FieldValue = Field(default_factory=unknown)
    brand: FieldValue = Field(default_factory=unknown)
    model: FieldValue = Field(default_factory=unknown)
    year: FieldValue = Field(default_factory=unknown)
    material: FieldValue = Field(default_factory=unknown)
    condition: FieldValue = Field(default_factory=unknown)
    quantity: FieldValue = Field(default_factory=unknown)
    quantity_unit: str | None = None
    price_min: FieldValue = Field(default_factory=unknown)
    price_max: FieldValue = Field(default_factory=unknown)
    attributes: list[FieldValue] = Field(default_factory=list)
    location_city: FieldValue = Field(default_factory=unknown)
    location_district: FieldValue = Field(default_factory=unknown)
    # "نقل عفش من الرياض للدمام": location_city is the origin, this is where it goes.
    destination_city: FieldValue = Field(default_factory=unknown)
    location_sensitivity: LocationSensitivity = LocationSensitivity.PREFERRED
    missing_decision_information: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    eligibility_groups: list[list[str]] = Field(default_factory=list, exclude=True)
    # When set, only this many of eligibility_groups must hit (long-tail phrases).
    eligibility_min: int | None = Field(default=None, exclude=True)
    # Groups that must hit in the ad title or seller name, not only the body.
    title_groups: list[list[str]] = Field(default_factory=list, exclude=True)
    result_unit: ResultUnit = ResultUnit.AD
    clarification_question: str | None = None
    understood: bool = False


class Seller(ContractModel):
    id: str
    name: str
    city: str | None = None
    district: str | None = None
    profile_url: str | None = None
    specialty_evidence: list[str] = Field(default_factory=list)


class Ad(ContractModel):
    id: str
    title: str
    description: str | None = None
    url: str | None = None
    city: str | None = None
    district: str | None = None
    price_amount: float | None = None
    price_currency: str | None = None
    posted_at: str | None = None
    image_ref: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    category_tags: list[str] = Field(default_factory=list)
    listing_state: str = "unknown"
    seller: Seller


class SearchResult(ContractModel):
    result_unit: ResultUnit
    ad: Ad | None = None
    seller: Seller | None = None
    score: float
    match_evidence: list[str] = Field(default_factory=list)
    # "exact" passed every eligibility rule. "near" is offered only when nothing passed: a
    # real listing that failed a soft rule (the head word not early in the title, or one of
    # several word groups missing). The app labels it as near, never as a match.
    match: str = "exact"


class NeedGroup(ContractModel):
    need: str
    intent: IntentResponse
    results: list[SearchResult] = Field(default_factory=list)
    state: SearchState = SearchState.RESULTS
    # Which of the customer's items this group answers, in the order M02 listed them. Items
    # are searched at the same time and finish in any order, so the position in the list
    # no longer says which item a group belongs to.
    need_index: int | None = None
    zero_reason: str | None = None


class SearchResponse(ContractModel):
    next_page: int | None = None
    contract_version: str = CONTRACT_VERSION
    state: SearchState
    intent: IntentResponse
    results: list[SearchResult] = Field(default_factory=list)
    groups: list[NeedGroup] = Field(default_factory=list)
    clarification_question: str | None = None
    trace_id: str
    # Why there is no exact result, when there is none: source_unavailable | timeout |
    # no_listings (the source returned nothing) | none_in_city (listings exist, none in the
    # asked city) | none_matching (listings exist, none match) | deleted. Null with results.
    zero_reason: str | None = None


class Attachment(ContractModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int


class Offer(ContractModel):
    amount: float | None = None
    currency: str | None = None
    note: str | None = None
    provider_name: str | None = None
    phone: str | None = None
    seller_id: str | None = None
    base_price: float | None = None
    delivery_included: bool | None = None
    delivery_price: float | None = None
    total_price: float | None = None
    need: str | None = None
    cheapest: bool = False
    quantity: int | None = None
    unit_price: float | None = None
    availability: str | None = None
    # «جديد» / «مستعمل», only when the supplier chose it on his offer form; a price read
    # from a free chat reply leaves it unknown rather than guessing from his words.
    condition: str | None = None
    created_at: str | None = None

    @model_serializer(mode='wrap')
    def serialize_optional_quote_metadata(self, handler):
        data=handler(self)
        for key in ('quantity','unit_price','availability'):
            if data.get(key) is None:
                data.pop(key,None)
        return data


class Message(ContractModel):
    id: str
    request_id: str
    sender_role: str
    seller_id: str | None = None
    need: str | None = None
    reply_to: str | None = None
    direction: str = "customer_to_seller"
    scope: str | None = None
    haraj_conversation_id: str | None = None
    body: str
    offer: Offer | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    created_at: str
    delivery_state: str | None = None
    deliveries: list[dict[str, Any]] = Field(default_factory=list)
    media: list[dict[str, Any]] = Field(default_factory=list)


class RequestRecipient(ContractModel):
    seller_id: str
    seller_name: str
    ad_id: str | None = None
    # The title of the seller's listing the invite names (from the search that showed it).
    ad_title: str | None = None
    need: str | None = None
    reply_token: str | None = None
    # queued | sending | sent | failed | unknown. «sent» means a channel accepted the
    # request (Haraj took the message, or it landed in the supplier's in-app inbox); it is
    # not delivered and not read. A row with no recorded outcome is «unknown», never «sent».
    send_status: str = "unknown"
    listing_url: str | None = None
    # When and by which channel (haraj | in_app) the request was accepted, from the
    # deliveries themselves. Null until then.
    sent_at: str | None = None
    channel: str | None = None
    # The supplier wrote back in this request's conversation.
    replied: bool = False


class RequestRecord(ContractModel):
    id: str
    owner_user_id: str
    original_text: str
    need: str | None
    notes: str | None = None
    city: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    recipients: list[RequestRecipient]
    attachments: list[Attachment] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    offers: list[Offer] = Field(default_factory=list)
    reply_token: str | None = None
    awarded_seller_id: str | None = None
    awarded_at: str | None = None
    # True only while the customer has chosen to give the winning supplier his phone.
    contact_shared: bool = False
    last_synced_at: str | None = None
    # «رقم الطلب» written into every message sent to a seller, so his replies are filed here.
    ref_code: str | None = None
    # When the customer first saw two or more priced offers side by side (POST /compared).
    compared_at: str | None = None
    # Whether the awarded supplier was told: state queued | sent | failed | not_requested | unknown.
    award_notice: dict[str, Any] | None = None
    # What happened after the award, as the customer told us: outcome completed |
    # not_completed, the total he actually paid, and his rating of the supplier.
    deal: dict[str, Any] | None = None
    # The customer's counter-offers, one per supplier offer; answered once the supplier
    # prices again after it.
    counters: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
