"""The OAuth side of the reporting connector (Flexee family reporting contract v1).

Authorization code with S256 PKCE for public clients: Claude's Client ID Metadata Document (read
only from claude.ai or claude.com) or Dynamic Client Registration. Codes are single-use for ten
minutes, access tokens last an hour and refresh tokens rotate on every use. Only administrators can
approve, and a connection stops working the moment its administrator stops being an active
administrator. Every code and token is stored only as a SHA-256 hash.

The sign-in page is the web app's (it holds the administrator's session); this module checks and
approves for it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import urllib.request
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from accounts.models import User
from .models import ReportingCall, ReportingClient, ReportingCode, ReportingConnection

SCOPE = "reporting:read"
ACCESS_MINUTES = 60
REFRESH_DAYS = 30
PRODUCT_NAME = "Flexee Marketing Management"
CLIENT_ID_PREFIX = "flexee-mm-"
API_PREFIX = "/meridian/api"
REDIRECT_HOSTS = ("claude.ai", "claude.com", "localhost", "127.0.0.1")
CLIENT_METADATA_HOSTS = ("claude.ai", "claude.com")
LOOPBACK = ("localhost", "127.0.0.1")
PKCE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _token(prefix: str) -> str:
    return prefix + secrets.token_hex(32)


def _s(v) -> str:
    """A form or JSON field as a string; anything else counts as missing."""
    return v if isinstance(v, str) else ""


def _clean_name(v):
    return re.sub(r"<[^>]*>", "", v)[:200] if isinstance(v, str) else None


def _fail(error: str, description: str):
    return False, {"error": error, "error_description": description}


# ------------------------------------------------------------------------------- addresses
def public_urls(request) -> dict:
    """The API's public address (for the connector's metadata and token endpoint) and the web
    app's (where administrators sign in). Without PUBLIC_API_URL, the address the request came in
    on is used."""
    proto = (request.META.get("HTTP_X_FORWARDED_PROTO") or request.scheme or "https").split(",")[0].strip()
    host = (request.META.get("HTTP_X_FORWARDED_HOST") or request.get_host() or "localhost").split(",")[0].strip()
    api = getattr(settings, "PUBLIC_API_URL", "") or f"{proto}://{host}"
    return {"api": api.rstrip("/"), "web": getattr(settings, "FRONTEND_URL", "http://localhost:3000").rstrip("/")}


def resource_url(urls: dict) -> str:
    """The connector's MCP address."""
    return f"{urls['api']}{API_PREFIX}/mcp/reporting"


def protected_resource_metadata(urls: dict) -> dict:  # RFC 9728
    return {
        "resource": resource_url(urls),
        "authorization_servers": [urls["api"]],
        "scopes_supported": [SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": f"{PRODUCT_NAME} reporting",
    }


def authorization_server_metadata(urls: dict) -> dict:  # RFC 8414
    """Sign-in happens on the web app, where the admin is signed in; tokens come from the API."""
    return {
        "issuer": urls["api"],
        "authorization_endpoint": f"{urls['web']}/oauth/reporting/authorize",
        "token_endpoint": f"{urls['api']}{API_PREFIX}/oauth/reporting/token",
        "registration_endpoint": f"{urls['api']}{API_PREFIX}/oauth/reporting/register",
        "scopes_supported": [SCOPE],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "client_id_metadata_document_supported": True,
    }


# ------------------------------------------------------------------------------- redirects
def redirect_allowed(uri: str) -> bool:
    """https on Claude's hosts, or a loopback address (Claude Code's local callback)."""
    try:
        u = urlparse(uri)
        host = (u.hostname or "").lower()
    except ValueError:
        return False
    if u.fragment or host not in REDIRECT_HOSTS:
        return False
    return u.scheme == "https" or (host in LOOPBACK and u.scheme == "http")


def redirect_matches(registered: list, uri: str) -> bool:
    """Exact match, or for a loopback address everything but the port (RFC 8252)."""
    for r in registered:
        if _same(r, uri):
            return True
        try:
            a, b = urlparse(r), urlparse(uri)
        except ValueError:
            continue
        if (a.hostname in LOOPBACK and a.scheme == b.scheme and a.hostname == b.hostname
                and a.path == b.path):
            return True
    return False


# ------------------------------------------------------------------------------- clients
def fetch_client_document(url: str) -> dict | None:
    """A client's metadata document. Replaced in tests."""
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as res:        # noqa: S310 (https, Claude hosts only)
        if res.status != 200:
            return None
        return json.loads(res.read(200_000))


def register(body: dict):
    """RFC 7591."""
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= 5:
        return _fail("invalid_redirect_uri", "Give one to five redirect URIs.")
    if not all(isinstance(u, str) and len(u) <= 500 and redirect_allowed(u) for u in uris):
        return _fail("invalid_redirect_uri", "This connector signs in only for Claude.")
    if (body.get("token_endpoint_auth_method") or "none") != "none":
        return _fail("invalid_client_metadata",
                     'Only public clients (token_endpoint_auth_method "none") are supported.')
    client_id = f"{CLIENT_ID_PREFIX}{secrets.token_hex(16)}"
    name = _clean_name(body.get("client_name"))
    ReportingClient.objects.create(client_id_hash=digest(client_id), client_id=client_id, kind="dcr",
                                   name=name, redirect_uris=uris)
    return True, {
        "client_id": client_id, "client_id_issued_at": int(timezone.now().timestamp()),
        "client_name": name, "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none", "scope": SCOPE,
    }


def get_client(client_id: str) -> ReportingClient | None:
    """A client registered here, or Claude's published identity read from its metadata document
    (cached a day)."""
    if not client_id or len(client_id) > 500:
        return None
    found = ReportingClient.objects.filter(client_id_hash=digest(client_id)).first()
    if found and (found.kind == "dcr" or found.updated_at > timezone.now() - timedelta(days=1)):
        return found
    try:
        url = urlparse(client_id)
    except ValueError:
        return None
    if url.scheme != "https" or (url.hostname or "").lower() not in CLIENT_METADATA_HOSTS:
        return None
    try:
        doc = fetch_client_document(client_id)
    except Exception:
        return found
    if doc is None:
        return found
    if not isinstance(doc, dict) or doc.get("client_id") != client_id or not isinstance(doc.get("redirect_uris"), list):
        return None
    uris = [u for u in doc["redirect_uris"] if isinstance(u, str) and len(u) <= 500 and redirect_allowed(u)]
    if not uris:
        return None
    row, _ = ReportingClient.objects.get_or_create(
        client_id_hash=digest(client_id), defaults={"client_id": client_id, "kind": "cimd"})
    row.name = _clean_name(doc.get("client_name"))
    row.redirect_uris = uris
    row.save()
    return row


def check(q: dict, urls: dict) -> dict:
    """Checks an authorization request. A bad client or return address is shown on our page
    (redirectable False); anything else goes back to the client as an OAuth error."""
    client = get_client(q.get("client_id") or "")
    if client is None:
        return {"error": "invalid_client", "redirectable": False,
                "description": f"This sign-in request comes from a client the {PRODUCT_NAME} reporting connector doesn't know."}
    redirect = q.get("redirect_uri") or ""
    if not redirect and len(client.redirect_uris) == 1:
        redirect = client.redirect_uris[0]
    if not redirect or not redirect_matches(client.redirect_uris, redirect):
        return {"error": "invalid_request", "redirectable": False,
                "description": "The sign-in request names a return address that wasn't registered."}
    base = {"client": client, "redirect": redirect, "redirectable": True}
    if q.get("response_type") != "code":
        return {**base, "error": "unsupported_response_type", "description": "Only response_type=code is supported."}
    if q.get("code_challenge_method") != "S256" or not PKCE.match(q.get("code_challenge") or ""):
        return {**base, "error": "invalid_request", "description": "A PKCE code_challenge with method S256 is required."}
    scopes = (q.get("scope") or "").split()
    if any(s not in (SCOPE, "offline_access") for s in scopes):
        return {**base, "error": "invalid_scope", "description": f"The only scope is {SCOPE}."}
    if q.get("resource") and q["resource"].rstrip("/") != resource_url(urls):
        return {**base, "error": "invalid_target", "description": f"The resource must be {resource_url(urls)}."}
    return base


def issue_code(client: ReportingClient, user_id: int, redirect: str, challenge: str) -> str:
    # Expired codes are dead weight; clear a day's worth on the way past.
    ReportingCode.objects.filter(expires_at__lt=timezone.now() - timedelta(days=1)).delete()
    code = secrets.token_hex(32)
    ReportingCode.objects.create(code_hash=digest(code), client=client, user_id=user_id, redirect_uri=redirect,
                                 code_challenge=challenge, scope=SCOPE,
                                 expires_at=timezone.now() + timedelta(minutes=10))
    return code


# ------------------------------------------------------------------------------- tokens
def token_endpoint(p: dict):
    if p.get("grant_type") == "authorization_code":
        return _exchange(p)
    if p.get("grant_type") == "refresh_token":
        return _refresh(p)
    return _fail("unsupported_grant_type", "Use authorization_code or refresh_token.")


def _tokens(access: str, refresh: str):
    return True, {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_MINUTES * 60,
                  "refresh_token": refresh, "scope": SCOPE}


def _exchange(p: dict):
    client = get_client(_s(p.get("client_id")))
    code = ReportingCode.objects.filter(code_hash=digest(_s(p.get("code")))).first()
    if client is None or code is None or code.client_id != client.id:
        return _fail("invalid_grant", "The code isn't valid.")
    # One use only: claim the code before checking the rest.
    claimed = ReportingCode.objects.filter(pk=code.pk, used_at__isnull=True).update(used_at=timezone.now())
    if claimed != 1 or code.expires_at < timezone.now():
        return _fail("invalid_grant", "The code has been used or has expired.")
    if _s(p.get("redirect_uri")) and not _same(code.redirect_uri, _s(p.get("redirect_uri"))):
        return _fail("invalid_grant", "The redirect_uri doesn't match the sign-in.")
    verifier = _s(p.get("code_verifier"))
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if not PKCE.match(verifier) or not _same(code.code_challenge, expected):
        return _fail("invalid_grant", "The PKCE code_verifier doesn't match.")
    if not is_admin(code.user_id):
        return _fail("invalid_grant", f"Only {PRODUCT_NAME} administrators can connect.")
    access, refresh = _token("flxa_"), _token("flxr_")
    now = timezone.now()
    ReportingConnection.objects.create(
        client=client, user_id=code.user_id, scope=code.scope,
        access_hash=digest(access), access_expires_at=now + timedelta(minutes=ACCESS_MINUTES),
        refresh_hash=digest(refresh), refresh_expires_at=now + timedelta(days=REFRESH_DAYS))
    ReportingClient.objects.filter(pk=client.pk).update(last_used_at=now)
    return _tokens(access, refresh)


def _refresh(p: dict):
    client = get_client(_s(p.get("client_id")))
    old = digest(_s(p.get("refresh_token")))
    now = timezone.now()
    c = ReportingConnection.objects.filter(refresh_hash=old, revoked_at__isnull=True,
                                           refresh_expires_at__gt=now).first()
    if client is None or c is None or c.client_id != client.id or not is_admin(c.user_id):
        return _fail("invalid_grant", "The refresh token isn't valid.")
    access, refresh = _token("flxa_"), _token("flxr_")
    # Rotate: the old refresh token stops working in the same write that issues the new one.
    rotated = ReportingConnection.objects.filter(pk=c.pk, refresh_hash=old).update(
        access_hash=digest(access), access_expires_at=now + timedelta(minutes=ACCESS_MINUTES),
        refresh_hash=digest(refresh), refresh_expires_at=now + timedelta(days=REFRESH_DAYS))
    if rotated != 1:
        return _fail("invalid_grant", "The refresh token has already been used.")
    return _tokens(access, refresh)


def authenticate(bearer: str | None) -> ReportingConnection | None:
    """The live connection a bearer token belongs to, while its administrator is still an active
    administrator."""
    if not bearer or not bearer.startswith("flxa_"):
        return None
    c = ReportingConnection.objects.filter(access_hash=digest(bearer), revoked_at__isnull=True,
                                           access_expires_at__gt=timezone.now()).first()
    if c is None or not is_admin(c.user_id):
        return None
    ReportingConnection.objects.filter(pk=c.pk).update(last_used_at=timezone.now())
    return c


def is_admin(user_id) -> bool:
    u = User.objects.filter(pk=user_id).first()
    return bool(u and u.is_active and u.is_admin)


# ------------------------------------------------------------------------------- admin screen
def active_connections() -> int:
    return ReportingConnection.objects.filter(revoked_at__isnull=True,
                                              refresh_expires_at__gt=timezone.now()).count()


def connections() -> list[dict]:
    """Admin > Reporting: every connection, newest first."""
    rows = list(ReportingConnection.objects.select_related("client", "user").order_by("-created_at")[:200])
    since = timezone.now() - timedelta(days=7)
    counts = dict(ReportingCall.objects.filter(connection__in=rows, created_at__gt=since)
                  .values_list("connection_id").annotate(n=Count("id")))
    now = timezone.now()
    return [{
        "id": r.id, "admin": r.user.email or None, "client": r.client.name or "Claude",
        "connected_at": r.created_at.isoformat(),
        "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
        "active": r.revoked_at is None and r.refresh_expires_at > now,
        "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
        "calls_7d": counts.get(r.id, 0),
    } for r in rows]


def revoke(connection_id, by_user_id) -> bool:
    return ReportingConnection.objects.filter(pk=connection_id, revoked_at__isnull=True).update(
        revoked_at=timezone.now(), revoked_by_id=by_user_id) == 1


def log_call(connection: ReportingConnection, tool: str, status: str, rows: int, ms: int) -> None:
    """One tool call, for Admin > Reporting's seven-day count. Never raises."""
    try:
        ReportingCall.objects.create(connection=connection, tool=tool, status=status, rows=rows, ms=ms)
    except Exception:  # pragma: no cover
        pass
