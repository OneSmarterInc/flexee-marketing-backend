"""The reporting connector's HTTP side (Flexee family reporting contract v1).

Machine-facing endpoints (no session): discovery metadata at the host root, client registration,
the token endpoint and the MCP endpoint. The sign-in page lives on the web app, where the
administrator is signed in; it calls the admin-only check, approve and deny endpoints here.
"""
from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import mcp, oauth

CALLS_PER_MINUTE = 120
_window: dict = {}
_lock = threading.Lock()


def limited(key: str) -> bool:
    """120 calls a minute per key. Held per process, so with several workers it is per worker."""
    now = time.monotonic()
    with _lock:
        start, count = _window.get(key, (0.0, 0))
        if not start or now - start >= 60:
            _window[key] = (now, 1)
            return False
        _window[key] = (start, count + 1)
        return count + 1 > CALLS_PER_MINUTE


def _ip(request) -> str:
    return (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR", "") or "?")


def _body(request) -> dict:
    """A JSON object or a form: the token endpoint is form-encoded per OAuth, registration is JSON."""
    ctype = request.META.get("CONTENT_TYPE", "")
    if "json" in ctype:
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    return {k: v for k, v in request.POST.items()}


def _no_store(response, *, pragma=False):
    response["Cache-Control"] = "no-store"
    if pragma:
        response["Pragma"] = "no-cache"
    return response


# ------------------------------------------------------------------------------- discovery
@require_GET
def protected_resource(request, *_):
    return JsonResponse(oauth.protected_resource_metadata(oauth.public_urls(request)))


@require_GET
def authorization_server(request, *_):
    return JsonResponse(oauth.authorization_server_metadata(oauth.public_urls(request)))


# ------------------------------------------------------------------------------- machine endpoints
@csrf_exempt
def register(request):
    if request.method != "POST":
        return HttpResponse(status=405, headers={"Allow": "POST"})
    if limited(f"register:{_ip(request)}"):
        return JsonResponse({"error": "rate_limited"}, status=429)
    ok, body = oauth.register(_body(request))
    return _no_store(JsonResponse(body, status=201 if ok else 400))


@csrf_exempt
def token(request):
    if request.method != "POST":
        return HttpResponse(status=405, headers={"Allow": "POST"})
    if limited(f"token:{_ip(request)}"):
        return JsonResponse({"error": "rate_limited"}, status=429)
    ok, body = oauth.token_endpoint(_body(request))
    return _no_store(JsonResponse(body, status=200 if ok else 400), pragma=True)


@csrf_exempt
def mcp_endpoint(request):
    if request.method != "POST":
        return HttpResponse("This MCP endpoint answers POST requests only.", status=405, headers={"Allow": "POST"})
    match = re.match(r"^Bearer\s+(.+)$", request.META.get("HTTP_AUTHORIZATION", ""), re.IGNORECASE)
    bearer = match.group(1).strip() if match else None
    connection = oauth.authenticate(bearer)
    if connection is None:
        urls = oauth.public_urls(request)
        challenge = (f'Bearer resource_metadata="{urls["api"]}/.well-known/oauth-protected-resource'
                     f'{oauth.API_PREFIX}/mcp/reporting", scope="{oauth.SCOPE}"')
        if bearer:
            challenge += ', error="invalid_token"'
        return JsonResponse({
            "error": "invalid_token",
            "error_description": f"Sign in with a {oauth.PRODUCT_NAME} administrator account to use the reporting connector.",
        }, status=401, headers={"WWW-Authenticate": challenge})
    if limited(f"mcp:{connection.id}"):
        return JsonResponse({"error": "rate_limited"}, status=429, headers={"Retry-After": "60"})
    try:
        message = json.loads(request.body or b"null")
    except ValueError:
        message = None
    if not isinstance(message, dict):
        return JsonResponse(mcp.error(None, -32700, "Send one JSON-RPC message as a JSON object."), status=400)
    reply = mcp.handle(message, connection)
    if reply is None:
        return HttpResponse(status=202)
    return JsonResponse(reply)


# ------------------------------------------------------------------------------- admin endpoints
def _back(redirect: str, params: dict) -> str:
    parts = urlparse(redirect)
    query = parse_qs(parts.query, keep_blank_values=True)
    flat = [(k, v) for k, vs in query.items() for v in vs]
    flat += [(k, v) for k, v in params.items() if v not in (None, "")]
    return urlunparse(parts._replace(query=urlencode(flat)))


class AdminOnly(APIView):
    """For the web app's sign-in page and Admin > Reporting. Administrators only, and the account
    must still be an active administrator."""
    permission_classes = [IsAuthenticated]

    def admin_or_refuse(self, request):
        if not oauth.is_admin(request.user.pk):
            return Response({"detail": f"Only {oauth.PRODUCT_NAME} administrators can connect Claude to reporting."},
                            status=403)
        return None


class Check(AdminOnly):
    def get(self, request):
        if (refused := self.admin_or_refuse(request)):
            return refused
        urls = oauth.public_urls(request)
        q = {k: v for k, v in request.query_params.items()}
        c = oauth.check(q, urls)
        if c.get("error") and not c["redirectable"]:
            return Response({"ok": False, "description": c["description"]})
        if c.get("error"):
            return Response({"ok": False, "description": c["description"],
                             "redirect": _back(c["redirect"], {"error": c["error"],
                                                               "error_description": c["description"],
                                                               "state": q.get("state")})})
        return Response({"ok": True, "client": c["client"].name or "Claude", "resource": oauth.resource_url(urls)})


class Approve(AdminOnly):
    def post(self, request):
        if (refused := self.admin_or_refuse(request)):
            return refused
        urls = oauth.public_urls(request)
        body = {k: v for k, v in request.data.items() if isinstance(v, str)}
        c = oauth.check(body, urls)
        if c.get("error"):
            return Response({"detail": c["description"]}, status=400)
        code = oauth.issue_code(c["client"], request.user.pk, c["redirect"], body["code_challenge"])
        return Response({"redirect": _back(c["redirect"], {"code": code, "state": body.get("state"), "iss": urls["api"]})})


class Deny(AdminOnly):
    def post(self, request):
        if (refused := self.admin_or_refuse(request)):
            return refused
        body = {k: v for k, v in request.data.items() if isinstance(v, str)}
        c = oauth.check(body, oauth.public_urls(request))
        if not c.get("redirectable"):
            return Response({"detail": c["description"]}, status=400)
        return Response({"redirect": _back(c["redirect"], {
            "error": "access_denied", "error_description": "The administrator declined.", "state": body.get("state")})})


class Connections(AdminOnly):
    def get(self, request):
        if (refused := self.admin_or_refuse(request)):
            return refused
        return Response({"connector_url": oauth.resource_url(oauth.public_urls(request)),
                         "connections": oauth.connections()})


class Revoke(AdminOnly):
    def post(self, request, pk):
        if (refused := self.admin_or_refuse(request)):
            return refused
        oauth.revoke(pk, request.user.pk)
        return Response({"connections": oauth.connections()})
