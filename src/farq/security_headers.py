"""Security headers on every response.

The policy allows exactly what the web app loads: its own files, Google Fonts, and the
Moyasar payment form (script + stylesheet from cdn.moyasar.com, API calls and 3-D Secure
frames on moyasar.com). Listing images come from Haraj's CDNs, so images may be any https
origin. Inline style attributes are used throughout app.js, hence 'unsafe-inline' for
styles only; scripts stay 'self' + Moyasar.
"""

from __future__ import annotations

import os

# Who may put Taseer in a frame. Farq's iOS app and site embed it on /taseer, and Capacitor
# serves the app from localhost (https:// on Android, capacitor:// on iOS).
#
# Never a wildcard host here. "https://*.vercel.app" would have named every site anyone has
# ever deployed to Vercel, and any one of them could have laid an invisible Taseer over its
# own buttons and had the customer send a request he never saw. x-frame-options is gone
# because it cannot express an allowlist at all; frame-ancestors is what browsers read.
#
# FARQ_FRAME_ANCESTORS replaces the whole list, for a new embedder (a preview host, another
# Farq surface) without a code change. Keep it to exact origins.
DEFAULT_FRAME_ANCESTORS = (
    "'self' https://farq.sa https://www.farq.sa "
    # Capacitor: https:// on Android, capacitor:// on iOS.
    "https://localhost capacitor://localhost "
    # Farq's Vite dev server, so /taseer shows the live product on a developer's machine
    # instead of a fallback. A page on a developer's own loopback is the only thing this
    # admits, which is why a port-specific loopback origin is not the wildcard risk.
    "http://localhost:5173 http://127.0.0.1:5173 "
    "https://farq-taseer-phi.vercel.app"
)


def frame_ancestors() -> str:
    value = (os.environ.get("FARQ_FRAME_ANCESTORS") or "").strip()
    return value or DEFAULT_FRAME_ANCESTORS


CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://cdn.moyasar.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.moyasar.com",
        # Farq's licensed Huwiya face is served once, from farq.sa (CORS *), so
        # Taseer renders in the same typeface as restaurants and grocery.
        "font-src 'self' data: https://fonts.gstatic.com https://www.farq.sa",
        "img-src 'self' data: blob: https:",
        "connect-src 'self' https://api.moyasar.com https://cdn.moyasar.com",
        "frame-src https://*.moyasar.com",
        "worker-src 'self'",
        "manifest-src 'self'",
        "form-action 'self' https://*.moyasar.com",
        "base-uri 'self'",
        "object-src 'none'",
        f"frame-ancestors {frame_ancestors()}",
    ]
)

HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
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
