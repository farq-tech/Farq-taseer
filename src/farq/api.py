"""HTTP API. Public search responses do not expose local-versus-live internals."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import re
from urllib.parse import quote
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict

from farq import push, subscriptions
from farq.cities import city_choices
from farq.config import PaymentsConfig, SearchConfig
from farq.contracts import Offer, RequestRecipient, SearchResult
from farq.corpus import MemoryCorpus, default_sample_path
from farq.idempotency import IdempotencyMiddleware
from farq.intent import analyze, analyze_needs, split_need_texts
from farq.haraj_chat import HarajChat, NotConnectedChat, chat_from_env
from farq.worker import dispatch_pending, start_poller, sync_replies
from farq.live_haraj import HarajLiveClient
from farq.media import fetch_thumb, listing_images
from farq.moyasar import MoyasarClient, verify_webhook_secret
from farq.orchestrator import iter_search, run_search
from farq.ratelimit import SlidingWindow, client_ip
from farq.security_headers import SecurityHeadersMiddleware
from farq.store import MAX_FILE_BYTES, MEDIA_TYPES, Store
from farq.subscriptions import PaymentsUnavailable, SubscriptionError

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
log = logging.getLogger("farq.api")

# Search input limits: one request fans out to Haraj once per item, so both are capped.
MAX_QUERY_CHARS = 500
MAX_NEEDS = 5
# Sign-in throttling, counted in the database so it holds across serverless instances.
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_PER_ACCOUNT = 10  # wrong passwords for one email from one address
LOGIN_MAX_PER_IP = 50  # wrong passwords from one address, any email


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterBody(ApiModel):
    email: str
    password: str
    name: str | None = None


EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
GUEST_DOMAIN = "@users.farq.local"


class SearchBody(ApiModel):
    query: str


class RecipientBody(ApiModel):
    seller_id: str
    seller_name: str
    ad_id: str | None = None
    need: str | None = None
    listing_url: str | None = None


class RequestBody(ApiModel):
    original_text: str
    need: str | None = None
    notes: str | None = None
    city: str | None = None
    attributes: dict = {}
    recipients: list[RecipientBody]


class MessageBody(ApiModel):
    body: str = ""
    sender_role: str = "user"
    seller_id: str | None = None
    seller_ids: list[str] | None = None
    media_ids: list[str] | None = None
    need: str | None = None
    reply_to: str | None = None


class SellerReplyBody(ApiModel):
    body: str = ""
    seller_id: str | None = None
    offer_amount: float | None = None
    offer_currency: str | None = None
    provider_name: str | None = None
    phone: str | None = None
    delivery_included: bool | None = None
    delivery_price: float | None = None


class AwardBody(ApiModel):
    seller_id: str
    notify: bool = True


class PushKeys(ApiModel):
    p256dh: str
    auth: str


class PushSubscriptionBody(ApiModel):
    endpoint: str
    keys: PushKeys


class CheckoutBody(ApiModel):
    plan: str


class VerifyBody(ApiModel):
    payment_id: str
    moyasar_payment_id: str


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
        payload = {
            "type": "intent",
            "trace_id": event["trace_id"],
            "intent": event["intent"].model_dump(mode="json"),
            "clarification_question": event.get("clarification_question"),
        }
        if event.get("intents"):
            payload["intents"] = [item.model_dump(mode="json") for item in event["intents"]]
        return payload
    if kind == "status":
        return {"type": "status", "state": event["state"], "trace_id": event["trace_id"]}
    if kind == "results":
        results: list[SearchResult] = event["results"]
        payload = {
            "type": "results",
            "trace_id": event["trace_id"],
            "state": event["state"].value,
            "partial": True,
            "results": [item.model_dump(mode="json") for item in results],
        }
        if event.get("need"):
            payload["need"] = event["need"]
        if event.get("groups"):
            payload["groups"] = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in event["groups"]]
        return payload
    if kind == "done":
        payload = event["response"].model_dump(mode="json")
        payload["type"] = "done"
        return payload
    return {"type": kind}


def _docs_enabled() -> bool:
    """API docs are for local work. On Vercel they are off unless FARQ_ENABLE_DOCS=1."""
    explicit = os.environ.get("FARQ_ENABLE_DOCS")
    if explicit is not None and explicit != "":
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    return not os.environ.get("VERCEL")


def _env_rate(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _hashed(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def create_app(
    store: Store,
    corpus: MemoryCorpus,
    live_client: HarajLiveClient | None,
    config: SearchConfig,
    payments: PaymentsConfig | None = None,
    moyasar: MoyasarClient | None = None,
    chat: HarajChat | None = None,
    expose_docs: bool | None = None,
) -> FastAPI:
    docs = _docs_enabled() if expose_docs is None else expose_docs
    app = FastAPI(
        title="FARQ Individuals",
        version="1",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.add_middleware(IdempotencyMiddleware, store=store)
    app.add_middleware(SecurityHeadersMiddleware)
    payments = payments or PaymentsConfig()
    moyasar = moyasar or MoyasarClient(payments.moyasar_secret_key, payments.moyasar_base_url)
    chat = chat or NotConnectedChat()
    live_keys = subscriptions.is_live_key(payments.moyasar_secret_key, payments.moyasar_publishable_key)
    # Per-instance throttles (see farq.ratelimit): searches fan out to Haraj, registrations
    # are the one place an unknown caller can probe which emails exist.
    search_limiter = SlidingWindow(_env_rate("FARQ_SEARCH_PER_MINUTE", 30), 60)
    intent_limiter = SlidingWindow(_env_rate("FARQ_INTENT_PER_MINUTE", 60), 60)
    register_limiter = SlidingWindow(_env_rate("FARQ_REGISTER_PER_HOUR", 20), 3600)

    def guard_search(request: Request, query: str, limiter: SlidingWindow) -> None:
        if not limiter.allow(client_ip(request)):
            raise HTTPException(status_code=429, detail="too many searches, try again in a minute", headers={"Retry-After": "60"})
        if len(query) > MAX_QUERY_CHARS:
            raise HTTPException(status_code=422, detail=f"query is too long (max {MAX_QUERY_CHARS} characters)")
        if len(split_need_texts(query)) > MAX_NEEDS:
            raise HTTPException(status_code=422, detail=f"too many items in one request (max {MAX_NEEDS})")

    def throttled_login(request: Request, email: str, password: str) -> str | None:
        """store.login behind the per-address and per-address+email failure counts."""
        ip = client_ip(request)
        ip_key, account_key = f"ip:{_hashed(ip)}", f"acct:{_hashed(ip + '|' + email)}"
        if store.login_failures(account_key, LOGIN_WINDOW_SECONDS) >= LOGIN_MAX_PER_ACCOUNT or store.login_failures(ip_key, LOGIN_WINDOW_SECONDS) >= LOGIN_MAX_PER_IP:
            raise HTTPException(status_code=429, detail="too many sign-in attempts, try again later", headers={"Retry-After": str(LOGIN_WINDOW_SECONDS)})
        token = store.login(email, password)
        if token is None:
            store.record_login_failure([ip_key, account_key])
        else:
            store.clear_login_failures(account_key)
        return token

    def current_account(authorization: str | None = Header(default=None)) -> dict:
        # Everyone signs in with a Taseer account; the old anonymous guest sessions no longer count.
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="authentication required")
        account = store.account_for_token(authorization.removeprefix("Bearer ").strip())
        if account is None or str(account["email"]).endswith(GUEST_DOMAIN):
            raise HTTPException(status_code=401, detail="invalid session")
        return account

    def current_user(account: dict = Depends(current_account)) -> str:
        return account["id"]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "contract_version": "1"}

    @app.get("/v1/cities")
    def cities() -> dict:
        return {"cities": city_choices()}

    @app.post("/v1/auth/register")
    def register(body: RegisterBody, request: Request) -> dict:
        if not register_limiter.allow(client_ip(request)):
            raise HTTPException(status_code=429, detail="too many attempts, try again later", headers={"Retry-After": "3600"})
        email = body.email.strip().lower()
        name = (body.name or "").strip()
        if not EMAIL.match(email) or email.endswith(GUEST_DOMAIN):
            raise HTTPException(status_code=422, detail="invalid email")
        if len(body.password) < 8:
            raise HTTPException(status_code=422, detail="password too short")
        if not 2 <= len(name) <= 60:
            raise HTTPException(status_code=422, detail="name required")
        try:
            user_id = store.register(email, body.password, name)
        except ValueError as exc:
            # The email has an account. Its owner (right password) is simply signed in; anyone
            # else gets the same generic refusal, counted like a wrong sign-in.
            token = throttled_login(request, email, body.password)
            account = store.account_for_token(token) if token else None
            if account is None or str(account["email"]).endswith(GUEST_DOMAIN):
                raise HTTPException(status_code=409, detail="could not create an account with these details") from exc
            return {"user_id": account["id"], "token": token, "name": account.get("name"), "email": email}
        token = store.login(email, body.password)
        return {"user_id": user_id, "token": token, "name": name, "email": email}

    @app.post("/v1/auth/login")
    def login(body: RegisterBody, request: Request) -> dict:
        token = throttled_login(request, body.email.strip().lower(), body.password)
        account = store.account_for_token(token) if token else None
        if account is None or str(account["email"]).endswith(GUEST_DOMAIN):
            raise HTTPException(status_code=401, detail="invalid credentials")
        return {"token": token, "name": account.get("name"), "email": account["email"]}

    @app.get("/v1/auth/me")
    def me(account: dict = Depends(current_account)) -> dict:
        return {"email": account["email"], "name": account.get("name")}

    @app.post("/v1/auth/logout")
    def logout(authorization: str | None = Header(default=None)) -> dict:
        if authorization and authorization.startswith("Bearer "):
            store.logout(authorization.removeprefix("Bearer ").strip())
        return {"signed_out": True}

    @app.post("/v1/intent")
    def intent_only(body: SearchBody, request: Request) -> dict:
        guard_search(request, body.query, intent_limiter)
        intents = analyze_needs(body.query)
        intent = intents[0] if intents else analyze(body.query)
        return {
            "intent": intent.model_dump(mode="json"),
            "intents": [item.model_dump(mode="json") for item in intents],
            "state": _intent_state(intent) if len(intents) <= 1 else None,
            "clarification_question": intent.clarification_question if len(intents) <= 1 else None,
        }

    def _user_from_header(authorization: str | None) -> str | None:
        if authorization and authorization.startswith("Bearer "):
            return store.user_for_token(authorization.removeprefix("Bearer ").strip())
        return None

    @app.post("/v1/search")
    def search(body: SearchBody, request: Request, authorization: str | None = Header(default=None)) -> dict:
        guard_search(request, body.query, search_limiter)
        response, trace = run_search(body.query, corpus, live_client, config)
        store.record_journey(response.trace_id, _user_from_header(authorization), body.query, response.state.value, trace)
        return response.model_dump(mode="json")

    @app.post("/v1/search/stream")
    def search_stream(body: SearchBody, request: Request, authorization: str | None = Header(default=None)):
        guard_search(request, body.query, search_limiter)
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
        # Only the account that ran the search can read its trace.
        trace = store.journey(trace_id, user_id)
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
    def create_request(body: RequestBody, background: BackgroundTasks, user_id: str = Depends(current_user)) -> dict:
        try:
            request_id = store.create_request(
                user_id,
                body.original_text,
                body.need,
                body.notes,
                body.city,
                body.attributes,
                [
                    RequestRecipient(
                        seller_id=item.seller_id,
                        seller_name=item.seller_name,
                        ad_id=item.ad_id,
                        need=item.need,
                        listing_url=item.listing_url,
                    )
                    for item in body.recipients
                ],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        background.add_task(dispatch_pending, store, chat, budget_seconds=1)
        record = store.get_request(request_id, user_id)
        return record.model_dump(mode="json")

    async def read_upload(file: UploadFile) -> tuple[str, bytes]:
        data = await file.read()
        content_type = (file.content_type or "").split(";")[0].strip()
        # Haraj's chat takes photos as JPEG and documents as PDF; the app converts photos before upload.
        if content_type not in MEDIA_TYPES:
            raise HTTPException(status_code=415, detail="send a photo or a PDF")
        if not data:
            raise HTTPException(status_code=422, detail="empty file")
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="file too large")
        return content_type, data

    @app.post("/v1/requests/{request_id}/files")
    async def upload_file(
        request_id: str,
        file: UploadFile = File(...),
        width: int | None = Form(default=None),
        height: int | None = Form(default=None),
        user_id: str = Depends(current_user),
    ) -> dict:
        content_type, data = await read_upload(file)
        try:
            return store.save_file(user_id, request_id, content_type, file.filename or "file", data, width, height)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="request not found") from exc

    @app.get("/v1/files/{file_id}")
    def download_file(file_id: str) -> Response:
        # The id is a random 128-bit capability, like a chat app's media link.
        found = store.get_file(file_id) if re.fullmatch(r"[0-9a-f]{32}", file_id) else None
        if found is None:
            raise HTTPException(status_code=404, detail="file not found")
        disposition = "inline" if found["content_type"].startswith("image/") else f"inline; filename*=UTF-8''{quote(found['filename'])}"
        return Response(
            found["data"],
            media_type=found["content_type"],
            headers={"Cache-Control": "private, max-age=31536000, immutable", "Content-Disposition": disposition},
        )

    @app.post("/v1/requests/{request_id}/attachments")
    async def upload(request_id: str, background: BackgroundTasks, file: UploadFile = File(...), user_id: str = Depends(current_user)) -> dict:
        """Photos added on the review screen go to every supplier on the request, like any other message."""
        content_type, data = await read_upload(file)
        try:
            saved = store.save_file(user_id, request_id, content_type, file.filename or "file", data)
            store.route_customer_message(request_id, user_id, "", media_ids=[saved["file_id"]], need=_single_need(request_id, user_id))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="request not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        background.add_task(dispatch_pending, store, chat, budget_seconds=1)
        return {"id": saved["file_id"], "filename": saved["name"], "content_type": content_type, "size_bytes": saved["size"], "url": saved["url"]}

    def _single_need(request_id: str, user_id: str) -> str | None:
        record = store.get_request(request_id, user_id)
        needs = {item.need for item in (record.recipients if record else [])}
        return next(iter(needs)) if len(needs) == 1 else None

    @app.post("/v1/requests/{request_id}/messages")
    def message(request_id: str, body: MessageBody, background: BackgroundTasks, user_id: str = Depends(current_user)) -> dict:
        try:
            created = store.route_customer_message(
                request_id,
                user_id,
                body.body.strip(),
                need=body.need,
                seller_id=body.seller_id,
                reply_to=body.reply_to,
                seller_ids=body.seller_ids,
                media_ids=body.media_ids,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="request not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        background.add_task(dispatch_pending, store, chat, budget_seconds=1)
        return created.model_dump(mode="json")

    @app.get("/v1/requests/{request_id}")
    def get_request(request_id: str, user_id: str = Depends(current_user)) -> dict:
        record = store.get_request(request_id, user_id)
        if record is None:
            raise HTTPException(status_code=404, detail="request not found")
        # Opening the conversation reads it.
        store.mark_read(request_id, user_id)
        return record.model_dump(mode="json")

    @app.get("/v1/seller/{token}")
    def seller_request(token: str) -> dict:
        view = store.seller_view(token)
        if view is None:
            raise HTTPException(status_code=404, detail="request not found")
        return view

    @app.post("/v1/requests/{request_id}/award")
    def award(request_id: str, body: AwardBody, background: BackgroundTasks, user_id: str = Depends(current_user)) -> dict:
        record = store.get_request(request_id, user_id)
        if record is None:
            raise HTTPException(status_code=404, detail="request not found")
        try:
            store.award(request_id, user_id, body.seller_id, body.notify)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        background.add_task(dispatch_pending, store, chat, budget_seconds=1)
        return store.get_request(request_id, user_id).model_dump(mode="json")

    @app.get("/v1/push/key")
    def push_key() -> dict:
        return {"public_key": push.public_key()}

    @app.post("/v1/push/subscribe")
    def push_subscribe(body: PushSubscriptionBody, user_id: str = Depends(current_user)) -> dict:
        if not push.allowed_endpoint(body.endpoint):
            raise HTTPException(status_code=422, detail="invalid endpoint")
        if len(body.keys.p256dh) > 256 or len(body.keys.auth) > 256:
            raise HTTPException(status_code=422, detail="invalid keys")
        if not store.add_push_subscription(user_id, body.endpoint, body.keys.p256dh, body.keys.auth):
            raise HTTPException(status_code=409, detail="this device is registered to another account")
        return {"subscribed": True}

    @app.post("/v1/seller/{token}/messages")
    def seller_reply(token: str, body: SellerReplyBody, background: BackgroundTasks) -> dict:
        if not body.body.strip() and body.offer_amount is None:
            raise HTTPException(status_code=422, detail="message or price is required")
        offer = None
        if body.offer_amount is not None:
            offer = Offer(
                amount=body.offer_amount,
                currency=body.offer_currency or "SAR",
                note=body.body.strip() or None,
                provider_name=body.provider_name,
                phone=body.phone,
                base_price=body.offer_amount,
                delivery_included=True if body.delivery_included is None else body.delivery_included,
                delivery_price=body.delivery_price or 0,
                total_price=None,
            )
        try:
            created = store.add_seller_reply(token, body.seller_id, body.body, offer)
        except ValueError as exc:
            status = 404 if str(exc) == "request not found" else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        background.add_task(push.notify_reply, store, created.request_id, created.seller_id, created.body)
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

    @app.get("/v1/subscriptions/plans")
    def subscription_plans() -> dict:
        # payments_available tells the app whether to offer upgrading at all: keys configured
        # and at least one plan that may take money with them.
        plans = subscriptions.list_plans(store, live=live_keys)
        available = payments.payments_configured and any(plan["purchasable"] for plan in plans)
        return {"plans": plans, "payments_available": available}

    @app.get("/v1/subscriptions/me")
    def subscription_me(user_id: str = Depends(current_user)) -> dict:
        return subscriptions.get_status(store, user_id)

    @app.post("/v1/subscriptions/checkout")
    def subscription_checkout(body: CheckoutBody, user_id: str = Depends(current_user)) -> dict:
        try:
            result = subscriptions.start_checkout(
                store,
                user_id=user_id,
                plan_code=body.plan,
                # Without the secret key a payment could be taken but never verified.
                publishable_key=payments.moyasar_publishable_key if payments.payments_configured else None,
                public_base_url=payments.public_base_url,
                live=live_keys,
            )
        except PaymentsUnavailable as exc:
            log.warning("checkout refused: %s", exc)
            raise HTTPException(status_code=503, detail="payments_unavailable") from exc
        except SubscriptionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "payment_id": result.payment_id,
            "plan": result.plan,
            "publishable_key": result.publishable_key,
            "amount": result.amount,
            "currency": result.currency,
            "callback_url": result.callback_url,
            "metadata": result.metadata,
        }

    @app.post("/v1/subscriptions/verify")
    def subscription_verify(body: VerifyBody, user_id: str = Depends(current_user)) -> dict:
        try:
            result = subscriptions.verify_checkout(
                store,
                moyasar,
                user_id=user_id,
                payment_id=body.payment_id,
                moyasar_payment_id=body.moyasar_payment_id,
            )
        except PaymentsUnavailable as exc:
            log.warning("verify refused: %s", exc)
            raise HTTPException(status_code=503, detail="payments_unavailable") from exc
        except SubscriptionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result.get("detail"):
            log.warning("verify %s: %s", result.get("reason"), result.pop("detail"))
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result.get("reason", "could not verify payment"))
        return result

    @app.post("/v1/payments/moyasar/webhook")
    async def moyasar_webhook(request: Request) -> dict:
        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001 - malformed webhook body
            raise HTTPException(status_code=400, detail="invalid payload") from exc
        headers = {key.lower(): value for key, value in request.headers.items()}
        if not verify_webhook_secret(payload, headers, payments.moyasar_webhook_secret):
            raise HTTPException(status_code=401, detail="invalid webhook secret")
        data = payload.get("data") or {}
        moyasar_payment_id = data.get("id")
        if not moyasar_payment_id:
            raise HTTPException(status_code=400, detail="missing payment id")
        event_type = payload.get("type", "unknown")
        event_id = payload.get("id") or f"{moyasar_payment_id}:{event_type}:{data.get('updated_at', '')}"
        metadata = data.get("metadata") or {}
        try:
            result = subscriptions.handle_webhook(
                store,
                moyasar,
                event_id=str(event_id),
                event_type=str(event_type),
                moyasar_payment_id=str(moyasar_payment_id),
                farq_payment_id=metadata.get("farq_payment_id"),
            )
        except PaymentsUnavailable as exc:
            # Moyasar retries a failed delivery; answer 503 so it does.
            log.error("webhook could not settle: %s", exc)
            raise HTTPException(status_code=503, detail="payments_unavailable") from exc
        if result.get("detail"):
            log.warning("webhook %s: %s", result.get("reason"), result.pop("detail"))
        return {"received": True, **result}

    # Registered before the web catch-all below, which answers every other GET.
    @app.get("/v1/internal/haraj-sync")
    def haraj_sync(authorization: str | None = Header(default=None)) -> dict:
        # Vercel Cron sends "Authorization: Bearer $CRON_SECRET".
        secret = os.environ.get("CRON_SECRET")
        if not secret:
            raise HTTPException(status_code=503, detail="CRON_SECRET is not set")
        if authorization != f"Bearer {secret}":
            raise HTTPException(status_code=401, detail="unauthorized")
        # One run a minute: up to three sends 20 s apart, then read replies.
        started = time.monotonic()
        sent = dispatch_pending(store, chat, budget_seconds=42)
        received = sync_replies(store, chat, budget_seconds=max(12.0, 54 - (time.monotonic() - started)))
        return {"sent": sent, "received": received}

    # Registered after every API route: an unknown /v1 path is a JSON 404, never the web app.
    @app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(rest: str) -> JSONResponse:
        del rest
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    if WEB_DIR.is_dir():

        # The functions run in Sydney, next to the database. Static files are cached at Vercel's edge
        # near the visitor (s-maxage; each deployment starts a fresh cache), and browsers revalidate
        # the page, script and styles so a deploy shows up at once.
        def static_file(path: Path) -> FileResponse:
            if path.suffix in {".png", ".svg", ".ico", ".woff2"}:
                cache = "public, max-age=86400, s-maxage=31536000"
            else:
                cache = "public, max-age=0, must-revalidate, s-maxage=31536000"
            return FileResponse(path, headers={"Cache-Control": cache})

        @app.get("/")
        def index() -> FileResponse:
            return static_file(WEB_DIR / "index.html")

        @app.get("/{full_path:path}")
        def spa(full_path: str) -> FileResponse:
            candidate = (WEB_DIR / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(WEB_DIR.resolve()):
                return static_file(candidate)
            return static_file(WEB_DIR / "index.html")

    return app


def default_data_dir() -> Path:
    configured = os.environ.get("FARQ_DATA_DIR")
    if configured:
        return Path(configured)
    # Vercel functions can write only under /tmp.
    if os.environ.get("VERCEL"):
        return Path("/tmp/farq")
    return Path("data/runtime")


def create_default_app() -> FastAPI:
    root = default_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    payments = PaymentsConfig()
    if payments.database_url:
        from farq.store_pg import PgStore

        store = PgStore(payments.database_url, root / "uploads")
    elif os.environ.get("VERCEL"):
        # /tmp on a Vercel function is per-instance and wiped on cold start.
        # Silently falling back here would mean every login, request and paid
        # subscription vanishes at random - refuse to boot instead.
        raise RuntimeError(
            "DATABASE_URL is not set. Refusing to run on Vercel against ephemeral "
            "/tmp storage - see docs/payments_setup.md for the Supabase connection string."
        )
    else:
        store = Store(root / "farq.sqlite3", root / "uploads")
    corpus = MemoryCorpus.from_json(Path(os.environ.get("FARQ_CORPUS_PATH", default_sample_path())))
    config = SearchConfig()
    live = HarajLiveClient(config) if config.enable_live else None
    moyasar = MoyasarClient(payments.moyasar_secret_key, payments.moyasar_base_url)
    # Taseer's own Haraj account from its own server settings; without them nothing is sent.
    chat = chat_from_env(cache=store)
    application = create_app(store, corpus, live, config, payments, moyasar, chat)
    # Serverless instances do not keep a thread alive; on Vercel the cron route drives the sync.
    if not os.environ.get("VERCEL"):
        start_poller(store, chat)
    return application


# Vercel imports this object and serves it as ASGI. A zero-argument factory
# is not an ASGI callable (scope, receive, send).
app = create_default_app()
