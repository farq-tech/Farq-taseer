"""HTTP API. Public search responses do not expose local-versus-live internals."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict

from farq.config import SearchConfig
from farq.contracts import Offer, RequestRecipient
from farq.corpus import MemoryCorpus, default_sample_path
from farq.live_haraj import HarajLiveClient
from farq.orchestrator import run_search
from farq.store import Store


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterBody(ApiModel):
    email: str
    password: str


class SearchBody(ApiModel):
    query: str


class RecipientBody(ApiModel):
    seller_id: str
    seller_name: str
    ad_id: str | None = None


class RequestBody(ApiModel):
    original_text: str
    need: str | None = None
    notes: str | None = None
    city: str | None = None
    attributes: dict = {}
    recipients: list[RecipientBody]


class MessageBody(ApiModel):
    body: str
    sender_role: str = "user"
    offer_amount: float | None = None
    offer_currency: str | None = None


def create_app(store: Store, corpus: MemoryCorpus, live_client: HarajLiveClient | None, config: SearchConfig) -> FastAPI:
    app = FastAPI(title="FARQ Individuals", version="1")

    def current_user(authorization: str | None = Header(default=None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="authentication required")
        user_id = store.user_for_token(authorization.removeprefix("Bearer ").strip())
        if user_id is None:
            raise HTTPException(status_code=401, detail="invalid session")
        return user_id

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "contract_version": "1"}

    @app.post("/v1/auth/register")
    def register(body: RegisterBody) -> dict:
        try:
            user_id = store.register(body.email, body.password)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="could not register") from exc
        token = store.login(body.email, body.password)
        return {"user_id": user_id, "token": token}

    @app.post("/v1/auth/login")
    def login(body: RegisterBody) -> dict:
        token = store.login(body.email, body.password)
        if token is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        return {"token": token}

    @app.post("/v1/search")
    def search(body: SearchBody, authorization: str | None = Header(default=None)) -> dict:
        user_id = None
        if authorization and authorization.startswith("Bearer "):
            user_id = store.user_for_token(authorization.removeprefix("Bearer ").strip())
        response, trace = run_search(body.query, corpus, live_client, config)
        store.record_journey(response.trace_id, user_id, body.query, response.state.value, trace)
        return response.model_dump(mode="json")

    @app.get("/v1/search/{trace_id}/trace")
    def search_trace(trace_id: str, user_id: str = Depends(current_user)) -> dict:
        del user_id
        trace = store.journey(trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="trace not found")
        return trace

    @app.post("/v1/requests")
    def create_request(body: RequestBody, user_id: str = Depends(current_user)) -> dict:
        try:
            request_id = store.create_request(
                user_id,
                body.original_text,
                body.need,
                body.notes,
                body.city,
                body.attributes,
                [RequestRecipient(seller_id=item.seller_id, seller_name=item.seller_name, ad_id=item.ad_id) for item in body.recipients],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record = store.get_request(request_id, user_id)
        return record.model_dump(mode="json")

    @app.post("/v1/requests/{request_id}/attachments")
    async def upload(request_id: str, file: UploadFile = File(...), user_id: str = Depends(current_user)) -> dict:
        if store.get_request(request_id, user_id) is None:
            raise HTTPException(status_code=404, detail="request not found")
        content = await file.read()
        attachment = store.add_attachment(request_id, user_id, file.filename or "file", file.content_type or "application/octet-stream", content)
        return attachment.model_dump(mode="json")

    @app.post("/v1/requests/{request_id}/messages")
    def message(request_id: str, body: MessageBody, user_id: str = Depends(current_user)) -> dict:
        if store.get_request(request_id, user_id) is None:
            raise HTTPException(status_code=404, detail="request not found")
        offer = None
        if body.offer_amount is not None:
            offer = Offer(amount=body.offer_amount, currency=body.offer_currency or "SAR")
        created = store.add_message(request_id, body.sender_role, user_id, body.body, offer)
        return created.model_dump(mode="json")

    @app.get("/v1/requests/{request_id}")
    def get_request(request_id: str, user_id: str = Depends(current_user)) -> dict:
        record = store.get_request(request_id, user_id)
        if record is None:
            raise HTTPException(status_code=404, detail="request not found")
        return record.model_dump(mode="json")

    return app


def app() -> FastAPI:
    root = Path(os.environ.get("FARQ_DATA_DIR", "data/runtime"))
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root / "farq.sqlite3", root / "uploads")
    corpus = MemoryCorpus.from_json(Path(os.environ.get("FARQ_CORPUS_PATH", default_sample_path())))
    config = SearchConfig()
    live = HarajLiveClient(config) if config.enable_live else None
    return create_app(store, corpus, live, config)
