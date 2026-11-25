"""Browser sessions kept in HttpOnly cookies.

A browser asks for this by sending `X-Session-Mode: cookie` when it signs in. The tokens are then set as cookies that
page scripts cannot read, and are left out of the response body. Any other client (tests, scripts) still gets the tokens
in the body and sends them in an Authorization header, as before.

Two portals can be signed in at once in one browser (the console and the customer site), so each has its own pair of
cookies. A request without an Authorization header is given one from the cookie of its portal: customer paths use the
customer cookie, everything else the operator cookie.

Cookies are sent by the browser on any request to the site, so a state-changing request that relies on a cookie must also
carry `X-Requested-With: payshield`. A page on another site cannot add that header without a CORS permission this service
never grants. The cookies are also SameSite=Strict."""
from http.cookies import SimpleCookie

from fastapi import Request, Response

from app.security import ACCESS_MINUTES, REFRESH_MINUTES

MODE_HEADER = "x-session-mode"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "payshield"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def portal_for_role(role: str) -> str:
    return "customer" if role == "customer" else "operator"


def portal_for_path(path: str) -> str:
    return "customer" if path.startswith("/api/customer/") else "operator"


def names(portal: str) -> tuple[str, str]:
    return f"ps_{portal}_at", f"ps_{portal}_rt"


def wants_cookies(request: Request) -> bool:
    return request.headers.get(MODE_HEADER, "").lower() == "cookie"


def _set(response: Response, name: str, value: str, minutes: int) -> None:
    from app.runtime import runtime

    response.set_cookie(
        name, value, max_age=minutes * 60, path="/", httponly=True, samesite="strict",
        secure=bool(runtime.settings and runtime.settings.cookie_secure),
    )


def set_access(response: Response, portal: str, token: str) -> None:
    _set(response, names(portal)[0], token, ACCESS_MINUTES)


def set_session(response: Response, portal: str, access: str, refresh: str) -> None:
    _set(response, names(portal)[0], access, ACCESS_MINUTES)
    _set(response, names(portal)[1], refresh, REFRESH_MINUTES)


def clear_session(response: Response, portal: str) -> None:
    from app.runtime import runtime

    for name in names(portal):
        response.delete_cookie(name, path="/", httponly=True, samesite="strict", secure=bool(runtime.settings and runtime.settings.cookie_secure))


class CookieAuth:
    """Turns a session cookie into an Authorization header for the route handlers, which only know headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope["headers"]
        raw = {k: v for k, v in headers}
        if b"authorization" not in raw and b"cookie" in raw:
            jar = SimpleCookie()
            try:
                jar.load(raw[b"cookie"].decode("latin-1"))
            except Exception:
                jar = SimpleCookie()
            morsel = jar.get(names(portal_for_path(scope["path"]))[0])
            if morsel is not None and morsel.value:
                if scope["method"] not in SAFE_METHODS and raw.get(CSRF_HEADER.encode(), b"").decode() != CSRF_VALUE:
                    return await self._refuse(send)
                scope = {**scope, "headers": [*headers, (b"authorization", f"Bearer {morsel.value}".encode())]}
        return await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(send):
        body = b'{"detail":"This request must come from the PayShield site."}'
        await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
