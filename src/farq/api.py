"""HTTP API. Public search responses do not expose local-versus-live internals."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict

from farq.cities import city_choices
from farq.config import SearchConfig
from farq.contracts import Offer, RequestRecipient, SearchResult
from farq.corpus import MemoryCorpus, default_sample_path
from farq.intent import analyze
from farq.live_haraj import HarajLiveClient
from farq.media import fetch_thumb, listing_images
from farq.orchestrator import iter_search, run_search
from farq.store import Store

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


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


class SellerReplyBody(ApiModel):
    body: str = ""
    seller_id: str | None = None
    offer_amount: float | None = None
    offer_currency: str | None = None


def _intent_state(intent) -> str | None:
    if not intent.understood:
        return "NOT_UNDERSTOOD"
    if isinstance(intent.location_city.value, list):
        return "LOCATION_AMBIGUOUS"
    if intent.clarification_question:
        return "CLARIFICATION_REQUIRED"
    return None


def _public_event(event: dict) -> dict:
    kind = event["type"]
    if kind == "intent":
        return {
            "type": "intent",
            "trace_id": event["trace_id"],
            "intent": event["intent"].model_dump(mode="json"),
            "clarification_question": event.get("clarification_question"),
        }
    if kind == "status":
        return {"type": "status", "state": event["state"], "trace_id": event["trace_id"]}
    if kind == "results":
        results: list[SearchResult] = event["results"]
        return {
            "type": "results",
            "trace_id": event["trace_id"],
            "state": event["state"].value,
            "partial": True,
            "results": [item.model_dump(mode="json") for item in results],
        }
    if kind == "done":
        payload = event["response"].model_dump(mode="json")
        payload["type"] = "done"
        return payload
    return {"type": kind}


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

    @app.get("/v1/cities")
    def cities() -> dict:
        return {"cities": city_choices()}

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

    @app.post("/v1/auth/guest")
    def guest() -> dict:
        return store.start_guest()

    @app.post("/v1/intent")
    def intent_only(body: SearchBody) -> dict:
        intent = analyze(body.query)
        return {
            "intent": intent.model_dump(mode="json"),
            "state": _intent_state(intent),
            "clarification_question": intent.clarification_question,
        }

    def _user_from_header(authorization: str | None) -> str | None:
        if authorization and authorization.startswith("Bearer "):
            return store.user_for_token(authorization.removeprefix("Bearer ").strip())
        return None

    @app.post("/v1/search")
    def search(body: SearchBody, authorization: str | None = Header(default=None)) -> dict:
        response, trace = run_search(body.query, corpus, live_client, config)
        store.record_journey(response.trace_id, _user_from_header(authorization), body.query, response.state.value, trace)
        return response.model_dump(mode="json")

    @app.post("/v1/search/stream")
    def search_stream(body: SearchBody, authorization: str | None = Header(default=None)):
        user_id = _user_from_header(authorization)

        def generate():
            for event in iter_search(body.query, corpus, live_client, config):
                if event["type"] == "done":
                    response = event["response"]
                    store.record_journey(response.trace_id, user_id, body.query, response.state.value, event["trace"])
                yield json.dumps(_public_event(event), ensure_ascii=False) + "\n"

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    @app.get("/v1/search/{trace_id}/trace")
    def search_trace(trace_id: str, user_id: str = Depends(current_user)) -> dict:
        del user_id
        trace = store.journey(trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="trace not found")
        return trace

    @app.get("/v1/listings/images")
    def images(url: str) -> dict:
        try:
            found = listing_images(url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"images": found}

    @app.get("/v1/media/thumb")
    def thumb(name: str, size: str = "400") -> Response:
        try:
            content, content_type = fetch_thumb(name, size)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="image not available") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="image source failed") from exc
        return Response(content, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/v1/requests")
    def list_requests(user_id: str = Depends(current_user)) -> dict:
        return {"requests": store.list_requests(user_id)}

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
        try:
            attachment = store.add_attachment(request_id, user_id, file.filename or "file", file.content_type or "application/octet-stream", content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return attachment.model_dump(mode="json")

    @app.get("/v1/requests/{request_id}/attachments/{attachment_id}")
    def download_attachment(request_id: str, attachment_id: str, user_id: str = Depends(current_user)) -> FileResponse:
        found = store.attachment_path(request_id, attachment_id, owner_user_id=user_id)
        if found is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        path, content_type, filename = found
        return FileResponse(path, media_type=content_type, filename=filename)

    @app.post("/v1/requests/{request_id}/messages")
    def message(request_id: str, body: MessageBody, user_id: str = Depends(current_user)) -> dict:
        if store.get_request(request_id, user_id) is None:
            raise HTTPException(status_code=404, detail="request not found")
        offer = None
        if body.offer_amount is not None:
            offer = Offer(amount=body.offer_amount, currency=body.offer_currency or "SAR")
        created = store.add_message(request_id, "user" if body.sender_role != "user" else body.sender_role, user_id, body.body, offer)
        return created.model_dump(mode="json")

    @app.get("/v1/requests/{request_id}")
    def get_request(request_id: str, user_id: str = Depends(current_user)) -> dict:
        record = store.get_request(request_id, user_id)
        if record is None:
            raise HTTPException(status_code=404, detail="request not found")
        return record.model_dump(mode="json")

    @app.get("/v1/seller/{token}")
    def seller_request(token: str) -> dict:
        view = store.seller_view(token)
        if view is None:
            raise HTTPException(status_code=404, detail="request not found")
        return view

    @app.post("/v1/seller/{token}/messages")
    def seller_reply(token: str, body: SellerReplyBody) -> dict:
        if not body.body.strip() and body.offer_amount is None:
            raise HTTPException(status_code=422, detail="message or price is required")
        offer = None
        if body.offer_amount is not None:
            offer = Offer(amount=body.offer_amount, currency=body.offer_currency or "SAR")
        try:
            created = store.add_seller_reply(token, body.seller_id, body.body, offer)
        except ValueError as exc:
            status = 404 if str(exc) == "request not found" else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return created.model_dump(mode="json")

    @app.get("/v1/seller/{token}/attachments/{attachment_id}")
    def seller_attachment(token: str, attachment_id: str) -> FileResponse:
        view = store.seller_view(token)
        if view is None:
            raise HTTPException(status_code=404, detail="request not found")
        row = store._request_by_token(token)
        found = store.attachment_path(row["id"], attachment_id, reply_token=token)
        if found is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        path, content_type, filename = found
        return FileResponse(path, media_type=content_type, filename=filename)

    if WEB_DIR.is_dir():

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

        @app.get("/{full_path:path}")
        def spa(full_path: str) -> FileResponse:
            candidate = (WEB_DIR / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(WEB_DIR.resolve()):
                return FileResponse(candidate)
            return FileResponse(WEB_DIR / "index.html")

    return app


def app() -> FastAPI:
    root = Path(os.environ.get("FARQ_DATA_DIR", "data/runtime"))
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root / "farq.sqlite3", root / "uploads")
    corpus = MemoryCorpus.from_json(Path(os.environ.get("FARQ_CORPUS_PATH", default_sample_path())))
    config = SearchConfig()
    live = HarajLiveClient(config) if config.enable_live else None
    return create_app(store, corpus, live, config)
