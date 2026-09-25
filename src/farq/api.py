"""HTTP API. Public search responses do not expose local-versus-live internals."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
import re
from urllib.parse import quote
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from farq import push, subscriptions
from farq.cities import city_choices
from farq.config import PaymentsConfig, SearchConfig
from farq import farq_auth
from farq.contracts import Offer, RequestRecipient, SearchResult
from farq.corpus import MemoryCorpus, default_sample_path
from farq.idempotency import IdempotencyMiddleware
from farq.intent import analyze, analyze_needs, split_need_texts
from farq.taxonomy import GROUPS, catalog as category_catalog, known_keys, read_business
from farq import mailer, notify
from farq import understand
from farq.haraj_chat import HarajChat, NotConnectedChat, _Prefixed, chat_from_env
from farq.worker import dispatch_pending, start_poller, sync_replies
from farq.live_haraj import HarajLiveClient
from farq.media import fetch_thumb, listing_images
from farq.moyasar import MoyasarClient, verify_webhook_secret
from farq.orchestrator import iter_search, run_search
from farq.limits import LimitExceeded, Limits, check_new_message, check_new_request, entitlement
from farq.ratelimit import SlidingWindow, client_ip
from farq.security_headers import SecurityHeadersMiddleware
from farq.store import MAX_FILE_BYTES, MEDIA_TYPES, AwardConflict, Store, search_seller_ids, seller_key
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


class FarqSessionBody(ApiModel):
    # The Farq access token the app holds; see farq.farq_auth.
    access_token: str


class LoginBody(ApiModel):
    email: str
    password: str
    name: str | None = None
    # A Farq session presented together with the Taseer password: both proofs at once tie
    # this Taseer account to that Farq account, and the Farq sign-in works from then on.
    farq_access_token: str | None = None


class SupplierRegisterBody(ApiModel):
    name: str
    email: str
    phone: str
    password: str
    activity_type: str = "both"
    description: str | None = None
    categories: list[str] = Field(default_factory=list, max_length=12)
    # An invite link proves which Haraj seller this is. Registration binds to that and never
    # to a seller id the caller names, so an account cannot claim someone else's requests.
    token: str | None = None


class SupplierLoginBody(ApiModel):
    email: str
    password: str


class SupplierClaimBody(ApiModel):
    token: str


class NotificationReadBody(ApiModel):
    id: str | None = None


class EmailVerifyBody(ApiModel):
    token: str


class PushBody(ApiModel):
    endpoint: str
    p256dh: str
    auth: str


class DescribeBody(ApiModel):
    text: str
    activity: str | None = None


class ShareContactBody(ApiModel):
    phone: str
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


SAUDI_MOBILE = re.compile(r"^(?:\+9665|009665|05)\d{8}$")


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
    # The search the recipients were picked from, for a search run before signing in.
    trace_id: str | None = None


class MessageBody(ApiModel):
    body: str = ""
    sender_role: str = "user"
    seller_id: str | None = None
    seller_ids: list[str] | None = None
    media_ids: list[str] | None = None
    need: str | None = None
    reply_to: str | None = None


MAX_OFFER = 10_000_000
PHONE = re.compile(r"^\+?[0-9]{9,15}$")


class SellerReplyBody(ApiModel):
    body: str = ""
    seller_id: str | None = None
    offer_amount: float | None = Field(default=None, gt=0, le=MAX_OFFER, allow_inf_nan=False)
    offer_currency: str | None = None
    # Accepted for older clients and ignored: the name on an offer is always the one we invited.
    provider_name: str | None = None
    phone: str | None = None
    delivery_included: bool | None = None
    delivery_price: float | None = Field(default=None, ge=0, le=MAX_OFFER, allow_inf_nan=False)


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
    limits: Limits | None = None,
    expose_docs: bool | None = None,
    farq_verifier=None,
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
    limits = limits or Limits()
    payments = payments or PaymentsConfig()
    moyasar = moyasar or MoyasarClient(payments.moyasar_secret_key, payments.moyasar_base_url)
    chat = chat or NotConnectedChat()
    # The model's reading of a sentence is kept in the store, so M02 (/v1/intent) pays for
    # it once and the search that follows finds it, on whichever instance it lands.
    understand.use_cache(_Prefixed(store, "understand:"))
    # Verifies a Farq session with Supabase; tests hand in a fake.
    verify_farq = farq_verifier or farq_auth.verify
    farq_configured = (lambda: True) if farq_verifier else farq_auth.configured
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

    @app.exception_handler(RequestValidationError)
    async def invalid_body(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # A body with Infinity or NaN is refused like any other bad value, not turned into a 500
        # by echoing the value back in the error.
        errors = [
            {key: value for key, value in error.items() if not (key == "input" and isinstance(value, float) and not math.isfinite(value))}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

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
        _send_verification(user_id, email, name)
        return {"user_id": user_id, "token": token, "name": name, "email": email,
                "verification_required": limits.verification_required()}

    # -- email verification -----------------------------------------------------
    # The gate is on the first send, not on sign-up: someone must be able to look at the
    # product before proving an address. See check_new_request in farq/limits.py.

    def _send_verification(user_id: str, email: str, name: str | None = None) -> str:
        if not limits.verification_required():
            return "not_required"
        token = store.start_email_verification(user_id)
        link = f"{payments.public_base_url.rstrip('/')}/verify?token={token}"
        greeting = f"أهلاً {name}،\n\n" if name else ""
        return mailer.send(
            email,
            "أكّد بريدك في فرق تسعير",
            f"{greeting}اضغط الرابط لتأكيد بريدك وتبدأ إرسال طلباتك. الرابط صالح ٤٨ ساعة."
            "\n\nإذا ما أنشأت هذا الحساب، تجاهل الرسالة.",
            url=link,
        )

    @app.post("/v1/auth/verify/send")
    def resend_verification(account: dict = Depends(current_account), request: Request = None) -> dict:
        if not register_limiter.allow(client_ip(request) if request else "unknown"):
            raise HTTPException(status_code=429, detail="too many attempts, try again later", headers={"Retry-After": "3600"})
        if store.email_verified(account["id"]):
            return {"verified": True, "sent": "already_verified"}
        return {"verified": False, "sent": _send_verification(account["id"], account["email"], account.get("name"))}

    @app.post("/v1/auth/verify")
    def confirm_verification(body: EmailVerifyBody) -> dict:
        user_id = store.verify_email(body.token.strip())
        if user_id is None:
            raise HTTPException(status_code=404, detail="invalid or expired link")
        return {"verified": True}

    @app.get("/v1/auth/verify/status")
    def verification_status(account: dict = Depends(current_account)) -> dict:
        return {
            "verified": store.email_verified(account["id"]),
            "required": limits.verification_required(),
            "email": account["email"],
        }

    @app.post("/v1/auth/login")
    def login(body: LoginBody, request: Request) -> dict:
        token = throttled_login(request, body.email.strip().lower(), body.password)
        account = store.account_for_token(token) if token else None
        if account is None or str(account["email"]).endswith(GUEST_DOMAIN):
            raise HTTPException(status_code=401, detail="invalid credentials")
        linked = None
        if body.farq_access_token:
            identity = verify_farq(body.farq_access_token) if farq_configured() else None
            if identity is None:
                linked = False
            else:
                linked = store.link_farq(account["id"], identity.user_id)
        return {"token": token, "name": account.get("name"), "email": account["email"], "farq_linked": linked}

    @app.get("/v1/auth/me")
    def me(account: dict = Depends(current_account)) -> dict:
        return {"email": account["email"], "name": account.get("name")}

    @app.post("/v1/auth/logout")
    def logout(authorization: str | None = Header(default=None)) -> dict:
        if authorization and authorization.startswith("Bearer "):
            store.logout(authorization.removeprefix("Bearer ").strip())
        return {"signed_out": True}

    @app.post("/v1/auth/farq")
    def farq_session(body: FarqSessionBody, request: Request) -> dict:
        """Sign in with the Farq account the customer already holds on farq.sa.

        The embed reads Farq's session and hands over its access token; Supabase confirms
        whose it is; the matching Taseer account (created or linked on first visit) gets a
        session. One login for one product - the reason the embed exists."""
        if not farq_configured():
            raise HTTPException(status_code=503, detail="Farq sign-in is not configured on this host")
        if not register_limiter.allow(client_ip(request)):
            raise HTTPException(status_code=429, detail="too many attempts, try again later", headers={"Retry-After": "3600"})
        identity = verify_farq(body.access_token)
        if identity is None:
            raise HTTPException(status_code=401, detail="Farq session is not valid")
        try:
            token = store.login_farq(identity.user_id, identity.email, identity.name, identity.email_verified)
        except ValueError as exc:
            # A Taseer account made with a password already carries this email, and Farq has
            # not confirmed the address. Its owner links the two by signing in once with that
            # password while the Farq session is present (POST /v1/auth/login).
            raise HTTPException(status_code=409, detail={"code": "TASEER_ACCOUNT_EXISTS", "message": "this email belongs to a Taseer account made with a password; sign in with it once to link the two"}) from exc
        account = store.account_for_token(token) or {}
        return {"token": token, "name": account.get("name"), "email": account.get("email", identity.email), "verification_required": False}

    # -- supplier accounts ----------------------------------------------------
    # A supplier who registers from an invite link is bound to the Haraj seller that link
    # proves and is active at once. A supplier who registers cold has nothing to prove who
    # he is, so his account is 'pending' and shows no requests until a link is claimed.

    def current_supplier(authorization: str | None = Header(default=None)) -> dict:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="authentication required")
        supplier = store.supplier_for_token(authorization.removeprefix("Bearer ").strip())
        if supplier is None:
            raise HTTPException(status_code=401, detail="invalid session")
        return supplier

    def _clean_supplier(body) -> tuple[str, str, str]:
        email = body.email.strip().lower()
        name = (body.name or "").strip()
        phone = re.sub(r"[\s-]", "", (body.phone or "").strip())
        if not EMAIL.match(email) or email.endswith(GUEST_DOMAIN):
            raise HTTPException(status_code=422, detail="invalid email")
        if not 2 <= len(name) <= 80:
            raise HTTPException(status_code=422, detail="name required")
        if not SAUDI_MOBILE.match(phone):
            raise HTTPException(status_code=422, detail="invalid phone")
        if len(body.password) < 8:
            raise HTTPException(status_code=422, detail="password too short")
        if body.activity_type not in {"both", "services", "products"}:
            raise HTTPException(status_code=422, detail="invalid activity type")
        return name, email, phone

    @app.get("/v1/supplier/categories")
    def supplier_categories(activity: str | None = None) -> dict:
        # The seed half of the taxonomy, grouped for the join screen. It is a starting
        # point, not the limit: whatever a supplier writes that falls outside it is kept
        # as a capability by /v1/supplier/describe and stays searchable.
        return {"categories": category_catalog(activity), "groups": list(GROUPS)}

    @app.post("/v1/supplier/describe")
    def supplier_describe(body: DescribeBody) -> dict:
        """Read the supplier's own business description: the categories it names, split
        into services and products, plus every other meaningful term he used."""
        return read_business(body.text or "", body.activity)

    @app.post("/v1/supplier/categories/suggest")
    def supplier_category_suggest(body: DescribeBody) -> dict:
        # Kept for the older client; the same reading, categories only.
        return {"categories": read_business(body.text or "", body.activity)["categories"]}

    @app.post("/v1/supplier/register")
    def supplier_register(body: SupplierRegisterBody, request: Request) -> dict:
        if not register_limiter.allow(client_ip(request)):
            raise HTTPException(status_code=429, detail="too many attempts, try again later", headers={"Retry-After": "3600"})
        name, email, phone = _clean_supplier(body)
        bound = store.seller_id_for_reply_token((body.token or "").strip()) if body.token else None
        # The description is the source of truth: it fills in whatever the supplier did not
        # tick, and it is the only thing that can produce capabilities. It is read without
        # the activity filter on purpose - a "services" supplier who writes "أركب سخانات"
        # has told us he works with water heaters, and his own words outrank a chip.
        read = read_business(body.description or "")
        known = known_keys()
        chosen = list(dict.fromkeys([key for key in body.categories if key in known]
                                    + [item["key"] for item in read["categories"]]))
        kinds = {item["key"]: item["kind"] for item in category_catalog()}
        try:
            supplier = store.register_supplier(
                name=name, email=email, phone=phone, password=body.password,
                activity_type=body.activity_type, description=body.description,
                categories=chosen,
                capabilities=read["capabilities"],
                services=[key for key in chosen if kinds.get(key) == "service"],
                products=[key for key in chosen if kinds.get(key) == "product"],
                haraj_seller_id=bound,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.track_supplier("registered", supplier["haraj_seller_id"], supplier_id=supplier["id"],
                             channel="in_app" if supplier["haraj_seller_id"] else "haraj")
        token = store.login_supplier(email, body.password)
        return {"token": token, "supplier": supplier}

    @app.post("/v1/supplier/login")
    def supplier_login(body: SupplierLoginBody, request: Request) -> dict:
        ip = client_ip(request)
        key = f"sup:{_hashed(ip + '|' + body.email.strip().lower())}"
        if store.login_failures(key, LOGIN_WINDOW_SECONDS) >= LOGIN_MAX_PER_ACCOUNT:
            raise HTTPException(status_code=429, detail="too many sign-in attempts, try again later", headers={"Retry-After": str(LOGIN_WINDOW_SECONDS)})
        token = store.login_supplier(body.email.strip().lower(), body.password)
        if token is None:
            store.record_login_failure([key])
            raise HTTPException(status_code=401, detail="invalid credentials")
        store.clear_login_failures(key)
        return {"token": token, "supplier": store.supplier_for_token(token)}

    @app.post("/v1/supplier/logout")
    def supplier_logout(authorization: str | None = Header(default=None)) -> dict:
        if authorization and authorization.startswith("Bearer "):
            store.logout_supplier(authorization.removeprefix("Bearer ").strip())
        return {"signed_out": True}

    @app.get("/v1/supplier/me")
    def supplier_me(supplier: dict = Depends(current_supplier)) -> dict:
        return {"supplier": supplier}

    @app.post("/v1/supplier/claim")
    def supplier_claim(body: SupplierClaimBody, supplier: dict = Depends(current_supplier)) -> dict:
        try:
            updated = store.claim_supplier_link(supplier["id"], body.token.strip())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if updated is None:
            raise HTTPException(status_code=404, detail="invalid link")
        store.track_supplier("registered", updated["haraj_seller_id"], supplier_id=updated["id"], channel="in_app")
        return {"supplier": updated}

    @app.get("/v1/supplier/notifications")
    def supplier_inbox(supplier: dict = Depends(current_supplier)) -> dict:
        return store.supplier_notifications(supplier["id"])

    @app.post("/v1/supplier/notifications/read")
    def supplier_inbox_read(body: NotificationReadBody, supplier: dict = Depends(current_supplier)) -> dict:
        return {"read": store.mark_supplier_notifications_read(supplier["id"], body.id)}

    @app.post("/v1/supplier/push")
    def supplier_push_subscribe(body: PushBody, supplier: dict = Depends(current_supplier)) -> dict:
        # Rung two of the ladder. The endpoint must be a browser's own push service, never
        # an address the client picked, or the server becomes a blind proxy.
        if not push.allowed_endpoint(body.endpoint):
            raise HTTPException(status_code=422, detail="unsupported push endpoint")
        store.save_supplier_push_subscription(supplier["id"], body.endpoint, body.p256dh, body.auth)
        return {"subscribed": True}

    @app.delete("/v1/supplier/push")
    def supplier_push_unsubscribe(endpoint: str, supplier: dict = Depends(current_supplier)) -> dict:
        del supplier
        store.remove_supplier_push_subscription(endpoint)
        return {"subscribed": False}

    @app.get("/v1/internal/supplier-funnel")
    def supplier_funnel_report(days: int = 30) -> dict:
        # invite_received -> opened -> registered -> request_viewed -> quote_submitted ->
        # buyer_replied -> awarded. Where a supplier stops is the number that decides
        # whether moving more of them onto the in-app lane is a gain or a loss.
        return store.supplier_funnel(max(1, min(days, 365)))

    @app.get("/v1/supplier/requests")
    def supplier_request_list(supplier: dict = Depends(current_supplier)) -> dict:
        rows = store.supplier_requests(supplier["id"])
        counts = {state: sum(1 for row in rows if row["state"] == state) for state in ("new", "quoted", "awarded", "lost")}
        return {"requests": rows, "counts": {**counts, "all": len(rows)}, "supplier": supplier}

    # -- contact sharing, by the customer, after an award ----------------------

    @app.post("/v1/requests/{request_id}/contact")
    def share_contact(request_id: str, body: ShareContactBody, user_id: str = Depends(current_user)) -> dict:
        phone = re.sub(r"[\s-]", "", body.phone.strip())
        if not SAUDI_MOBILE.match(phone):
            raise HTTPException(status_code=422, detail="invalid phone")
        try:
            return store.share_contact(request_id, user_id, phone=phone, lat=body.lat, lng=body.lng)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="request not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/v1/requests/{request_id}/contact")
    def revoke_contact(request_id: str, user_id: str = Depends(current_user)) -> dict:
        try:
            return store.revoke_contact(request_id, user_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="request not found") from exc

    @app.post("/v1/intent")
    def intent_only(body: SearchBody, request: Request, background: BackgroundTasks) -> dict:
        guard_search(request, body.query, intent_limiter)
        # M02 answers from the rules at once (half a second). The model's reading of the same
        # sentence is started here and kept in the shared store, so the search that follows a
        # few seconds later finds it ready instead of waiting for it. Measured on production:
        # reading on M02 itself put M02 at 2.3-3s for a first result only 0.5s sooner.
        intents = analyze_needs(body.query)
        if understand.enabled():
            background.add_task(understand.read_query, body.query)
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

    def _remember_sellers(trace_id: str, user_id: str | None, results) -> None:
        # A quote request may only go to sellers a search showed (TSR-014).
        store.record_search_sellers(trace_id, user_id, sorted(search_seller_ids(results)))

    def _response_results(response) -> list:
        return [*response.results, *(result for group in response.groups for result in group.results)]

    @app.post("/v1/search")
    def search(body: SearchBody, request: Request, authorization: str | None = Header(default=None)) -> dict:
        guard_search(request, body.query, search_limiter)
        response, trace = run_search(body.query, corpus, live_client, config)
        user_id = _user_from_header(authorization)
        store.record_journey(response.trace_id, user_id, body.query, response.state.value, trace)
        _remember_sellers(response.trace_id, user_id, _response_results(response))
        return response.model_dump(mode="json")

    @app.post("/v1/search/stream")
    def search_stream(body: SearchBody, request: Request, authorization: str | None = Header(default=None)):
        guard_search(request, body.query, search_limiter)
        user_id = _user_from_header(authorization)

        def generate():
            for event in iter_search(body.query, corpus, live_client, config):
                if event["type"] == "results":
                    # The app lets customers pick from a batch before the search ends.
                    _remember_sellers(event["trace_id"], user_id, event["results"])
                if event["type"] == "done":
                    response = event["response"]
                    store.record_journey(response.trace_id, user_id, body.query, response.state.value, event["trace"])
                    _remember_sellers(response.trace_id, user_id, _response_results(response))
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
            check_new_request(store, limits, user_id, body.recipients, body.need, body.trace_id)
        except LimitExceeded as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
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
        # Registered suppliers are told here, in the app, because nothing will be sent to
        # them through Haraj. The rest are reached by the worker, as before.
        background.add_task(
            notify.notify_sellers, store, [item.seller_id for item in body.recipients],
            "request_new", request_id=request_id, need=body.need,
        )
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
            check_new_message(store, limits, user_id)
        except LimitExceeded as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
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
            check_new_message(store, limits, user_id)
        except LimitExceeded as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
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
        # A message to a supplier who has already priced is an answer to him; to one who
        # has not, it is a question about the job.
        record = store.get_request(request_id, user_id)
        priced = {seller_key(offer.seller_id) for offer in (record.offers if record else [])}
        # Who this message actually reached: one supplier when it was a reply to him, else
        # everyone on the item, else - with no item named - everyone on the request.
        if created.seller_id:
            targets = [created.seller_id]
        else:
            targets = [
                item.seller_id
                for item in (record.recipients if record else [])
                if created.need is None or (item.need or None) == created.need
            ]
        for seller in targets:
            event = "buyer_reply" if seller_key(seller) in priced else "question_new"
            background.add_task(
                notify.notify_sellers, store, [seller], event,
                request_id=request_id, need=created.need, body=created.body[:140] or None,
            )
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
    def seller_request(token: str, authorization: str | None = Header(default=None)) -> dict:
        view = store.seller_view(token)
        if view is None:
            raise HTTPException(status_code=404, detail="request not found")
        seller = store.seller_id_for_reply_token(token)
        supplier = None
        if authorization and authorization.startswith("Bearer "):
            supplier = store.supplier_for_token(authorization.removeprefix("Bearer ").strip())
        # Opening the invite is the step an unregistered supplier reaches; a supplier with an
        # account who opens the same screen has gone one further and viewed the request.
        store.track_supplier("opened", seller, request_id=view.get("request_id"), need=view.get("need"))
        if supplier is None and seller:
            # He has shown up, so stop treating him as a stranger next time. The record is
            # created from what the link already proves and grants nothing by itself: no
            # session is issued here, so a forwarded link still opens this one request and
            # not his inbox. He keeps receiving the Haraj invite until a channel that can
            # reach him exists.
            # A reply token resolves to exactly one recipient, so that entry is his own row.
            mine = (view.get("recipients") or [{}])[0]
            guest = store.ensure_guest_supplier(seller, mine.get("seller_name"))
            if guest is not None and guest.get("status") == "guest":
                store.track_supplier("account_created", seller, supplier_id=guest["id"],
                                     request_id=view.get("request_id"), need=view.get("need"))
        if supplier:
            store.track_supplier("request_viewed", seller, request_id=view.get("request_id"),
                                 supplier_id=supplier["id"], need=view.get("need"), channel="in_app")
        return view

    @app.post("/v1/requests/{request_id}/award")
    def award(request_id: str, body: AwardBody, background: BackgroundTasks, user_id: str = Depends(current_user)) -> dict:
        record = store.get_request(request_id, user_id)
        if record is None:
            raise HTTPException(status_code=404, detail="request not found")
        try:
            store.award(request_id, user_id, body.seller_id, body.notify)
        except AwardConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.track_supplier("awarded", body.seller_id, request_id=request_id)
        background.add_task(notify.notify_sellers, store, [body.seller_id], "awarded", request_id=request_id)
        others = [item.seller_id for item in record.recipients if seller_key(item.seller_id) != seller_key(body.seller_id)]
        if others:
            background.add_task(notify.notify_sellers, store, others, "request_cancelled", request_id=request_id)
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
        phone = re.sub(r"[\s-]", "", body.phone or "") or None
        if phone is not None and not PHONE.match(phone):
            raise HTTPException(status_code=422, detail="invalid phone number")
        offer = None
        if body.offer_amount is not None:
            offer = Offer(
                amount=body.offer_amount,
                currency=body.offer_currency or "SAR",
                note=body.body.strip() or None,
                provider_name=None,
                phone=phone,
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
        if offer is not None:
            store.track_supplier("quote_submitted", created.seller_id or body.seller_id,
                                 request_id=created.request_id, need=created.need)
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
        status = subscriptions.get_status(store, user_id)
        # The app shows what is left before it lets someone pick suppliers, so the same
        # numbers the server enforces travel with the status.
        status["entitlement"] = entitlement(store, limits, user_id).as_dict()
        return status

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
        return {"sent": sent, "received": received, "queue": _watch_queue(), "closing": _ring_closing_soon()}

    # The backlog belongs to the Haraj lane only: the in-app lane has no queue. A backlog is
    # a clock, because the whole platform sends three contacts a minute, so it is measured in
    # how long it would take to drain rather than in rows.
    QUEUE_ALERT_MINUTES = int(os.environ.get("FARQ_QUEUE_ALERT_MINUTES", "45"))

    def _watch_queue() -> dict:
        health = store.queue_health()
        breached = health["drain_minutes"] >= QUEUE_ALERT_MINUTES or health["send_paused"]
        health["alert"] = breached
        if breached:
            log.warning(
                "haraj backlog: %s queued, ~%s min to drain, paused=%s, in-app share 30d=%s",
                health["queued"], health["drain_minutes"], health["send_paused"], health["in_app_share_30d"],
            )
            where = os.environ.get("OPS_EMAIL", "").strip()
            if where:
                mailer.send(
                    where,
                    "تنبيه: طابور إرسال فرق",
                    f"في الطابور {health['queued']} رسالة، وتحتاج ~{health['drain_minutes']} دقيقة للتصريف."
                    f" الإرسال موقوف: {health['send_paused']}."
                    f" نسبة التسليم داخل التطبيق آخر 30 يوم: {health['in_app_share_30d']}.",
                )
        return health

    def _ring_closing_soon() -> int:
        """A supplier who has not priced a request the customer is already deciding on is
        about to miss it. Rung one dedupes, so this is safe to run every minute."""
        rung = 0
        for row in store.requests_closing_soon():
            rung += notify.notify_sellers(
                store, [row["seller_id"]], "closing_soon", request_id=row["request_id"], need=row.get("need"),
            )
        return rung

    @app.get("/v1/internal/queue-health")
    def queue_health_report() -> dict:
        return _watch_queue()

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
