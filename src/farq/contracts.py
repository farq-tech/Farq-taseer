"""Versioned contracts for the consumer journey. Unknown values stay null."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    type: FieldValue = Field(default_factory=unknown)
    category: FieldValue = Field(default_factory=unknown)
    subcategory: FieldValue = Field(default_factory=unknown)
    brand: FieldValue = Field(default_factory=unknown)
    model: FieldValue = Field(default_factory=unknown)
    year: FieldValue = Field(default_factory=unknown)
    material: FieldValue = Field(default_factory=unknown)
    condition: FieldValue = Field(default_factory=unknown)
    quantity: FieldValue = Field(default_factory=unknown)
    price_min: FieldValue = Field(default_factory=unknown)
    price_max: FieldValue = Field(default_factory=unknown)
    attributes: list[FieldValue] = Field(default_factory=list)
    location_city: FieldValue = Field(default_factory=unknown)
    location_district: FieldValue = Field(default_factory=unknown)
    location_sensitivity: LocationSensitivity = LocationSensitivity.PREFERRED
    missing_decision_information: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    eligibility_groups: list[list[str]] = Field(default_factory=list, exclude=True)
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
    category_tags: list[str] = Field(default_factory=list)
    listing_state: str = "unknown"
    seller: Seller


class SearchResult(ContractModel):
    result_unit: ResultUnit
    ad: Ad | None = None
    seller: Seller | None = None
    score: float
    match_evidence: list[str] = Field(default_factory=list)


class SearchResponse(ContractModel):
    contract_version: str = CONTRACT_VERSION
    state: SearchState
    intent: IntentResponse
    results: list[SearchResult] = Field(default_factory=list)
    clarification_question: str | None = None
    trace_id: str


class Attachment(ContractModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int


class Offer(ContractModel):
    amount: float | None = None
    currency: str | None = None
    note: str | None = None


class Message(ContractModel):
    id: str
    request_id: str
    sender_role: str
    body: str
    offer: Offer | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    created_at: str


class RequestRecipient(ContractModel):
    seller_id: str
    seller_name: str
    ad_id: str | None = None


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
    created_at: str
