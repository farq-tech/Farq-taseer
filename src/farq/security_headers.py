"""Security headers on every response.

The policy allows exactly what the web app loads: its own files, Google Fonts, and the
Moyasar payment form (script + stylesheet from cdn.moyasar.com, API calls and 3-D Secure
frames on moyasar.com). Listing images come from Haraj's CDNs, so images may be any https
origin. Inline style attributes are used throughout app.js, hence 'unsafe-inline' for
styles only; scripts stay 'self' + Moyasar.
"""

from __future__ import annotations

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://cdn.moyasar.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.moyasar.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "img-src 'self' data: blob: https:",
        "connect-src 'self' https://api.moyasar.com https://cdn.moyasar.com",
        "frame-src https://*.moyasar.com",
        "worker-src 'self'",
        "manifest-src 'self'",
        "form-action 'self' https://*.moyasar.com",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
    ]
)

HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"x-frame-options", b"DENY"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(self)"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message):
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                message = {**message, "headers": [*message.get("headers", []), *[(name, value) for name, value in HEADERS if name not in present]]}
            await send(message)

        await self.app(scope, receive, with_headers)
