"""The reporting connector: discovery, the OAuth flow, the MCP endpoint and the six views."""
import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Role, User
from flexee_engine import api
from games import models
from games.schedule import build_schedule
from games.scheduler import SWEEP_KEY
from . import oauth, reports, views
from .models import ReportingCall, ReportingConnection

API = "/meridian/api"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def _admin(name="boss"):
    return User.objects.create_user(username=name, password="x", role=Role.ADMIN, email=f"{name}@example.com")


def _bearer(user):
    token, _ = Token.objects.get_or_create(user=user)
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return c


def _game(name="Torque 101", faculty=None, tz="America/New_York", first_due="2099-03-10", **kw):
    faculty = faculty or User.objects.create_user(username=f"f{User.objects.count()}", password="x", role=Role.FACULTY,
                                                  email=f"f{User.objects.count()}@example.com")
    first = date.fromisoformat(first_due) - timedelta(days=6)
    game = models.Game.objects.create(
        name=name, faculty=faculty, num_rounds=14, takeover_round=4, current_round=2, timezone=tz,
        config_snapshot=api.default_config(), start_date=first, schedule=build_schedule(first, 7, 14),
        status=models.GameStatus.RUNNING, deployed=True, student_deployed=True,
        student_deployed_at=timezone.now(), **kw)
    game.faculty_members.add(faculty)
    return game


class Connected:
    """A client with a live connection (access and refresh token)."""

    def __init__(self, testcase, admin=None):
        self.t = testcase
        self.admin = admin or _admin()
        self.web = _bearer(self.admin)
        self.http = APIClient()
        self.client_id = self.t.register_client()
        redirect = self._approve()
        code = parse_qs(urlparse(redirect).query)["code"][0]
        r = self.http.post(f"{API}/oauth/reporting/token", {
            "grant_type": "authorization_code", "client_id": self.client_id, "code": code,
            "redirect_uri": REDIRECT, "code_verifier": VERIFIER}, format="multipart")
        assert r.status_code == 200, r.content
        self.tokens = r.json()

    def _approve(self):
        r = self.web.post(f"{API}/oauth/reporting/approve", {
            "client_id": self.client_id, "redirect_uri": REDIRECT, "response_type": "code",
            "code_challenge": CHALLENGE, "code_challenge_method": "S256", "scope": oauth.SCOPE, "state": "xyz"},
            format="json")
        assert r.status_code == 200, r.content
        return r.json()["redirect"]

    def rpc(self, method, params=None, id_=1, token=None):
        body = {"jsonrpc": "2.0", "id": id_, "method": method}
        if params is not None:
            body["params"] = params
        return self.http.post(f"{API}/mcp/reporting", body, format="json",
                              HTTP_AUTHORIZATION=f"Bearer {token or self.tokens['access_token']}")

    def call(self, tool):
        r = self.rpc("tools/call", {"name": tool, "arguments": {}})
        assert r.status_code == 200, r.content
        return r.json()["result"]["structuredContent"]["rows"]


class ReportingTestCase(TestCase):
    def register_client(self, uris=None):
        r = APIClient().post(f"{API}/oauth/reporting/register", {
            "client_name": "Claude", "redirect_uris": uris or [REDIRECT], "token_endpoint_auth_method": "none"},
            format="json")
        assert r.status_code == 201, r.content
        return r.json()["client_id"]


@override_settings(ALLOWED_HOSTS=["*"])
class DiscoveryTests(ReportingTestCase):
    def test_protected_resource_metadata_both_forms(self):
        for path in ("/.well-known/oauth-protected-resource",
                     "/.well-known/oauth-protected-resource/meridian/api/mcp/reporting"):
            d = APIClient().get(path, HTTP_HOST="api.example.com", secure=True).json()
            self.assertEqual(d["resource"], "https://api.example.com/meridian/api/mcp/reporting")
            self.assertEqual(d["authorization_servers"], ["https://api.example.com"])
            self.assertEqual(d["scopes_supported"], ["reporting:read"])
            self.assertEqual(d["bearer_methods_supported"], ["header"])

    @override_settings(PUBLIC_API_URL="https://marketing.example.org/", FRONTEND_URL="https://app.example.org")
    def test_authorization_server_metadata_both_forms(self):
        for path in ("/.well-known/oauth-authorization-server",
                     "/.well-known/oauth-authorization-server/meridian/api/oauth/reporting"):
            d = APIClient().get(path).json()
            self.assertEqual(d["issuer"], "https://marketing.example.org")
            self.assertEqual(d["authorization_endpoint"], "https://app.example.org/oauth/reporting/authorize")
            self.assertEqual(d["token_endpoint"], "https://marketing.example.org/meridian/api/oauth/reporting/token")
            self.assertEqual(d["registration_endpoint"], "https://marketing.example.org/meridian/api/oauth/reporting/register")
            self.assertEqual(d["code_challenge_methods_supported"], ["S256"])
            self.assertEqual(d["token_endpoint_auth_methods_supported"], ["none"])
            self.assertTrue(d["client_id_metadata_document_supported"])

    def test_the_request_host_is_used_without_a_public_url(self):
        d = APIClient().get("/.well-known/oauth-protected-resource", HTTP_HOST="x.test",
                            HTTP_X_FORWARDED_PROTO="https", HTTP_X_FORWARDED_HOST="real.example.com").json()
        self.assertEqual(d["resource"], "https://real.example.com/meridian/api/mcp/reporting")


class RegistrationTests(ReportingTestCase):
    def reg(self, **body):
        return APIClient().post(f"{API}/oauth/reporting/register", body, format="json")

    def test_a_valid_registration(self):
        r = self.reg(client_name="<b>Claude</b>", redirect_uris=[REDIRECT, "http://127.0.0.1:5555/cb"])
        self.assertEqual(r.status_code, 201)
        d = r.json()
        self.assertTrue(d["client_id"].startswith("flexee-mm-"))
        self.assertEqual(d["client_name"], "Claude")                       # markup stripped
        self.assertEqual(d["token_endpoint_auth_method"], "none")
        self.assertEqual(r["Cache-Control"], "no-store")

    def test_redirects_only_for_claude_or_loopback(self):
        for uris in (["https://evil.example.com/cb"], ["http://claude.ai/cb"], [REDIRECT + "#frag"],
                     [], ["https://claude.ai/a"] * 6, ["not a url"], [5]):
            r = self.reg(redirect_uris=uris)
            self.assertEqual(r.status_code, 400, uris)
            self.assertEqual(r.json()["error"], "invalid_redirect_uri")

    def test_only_public_clients(self):
        r = self.reg(redirect_uris=[REDIRECT], token_endpoint_auth_method="client_secret_basic")
        self.assertEqual(r.json()["error"], "invalid_client_metadata")


class AuthorizeTests(ReportingTestCase):
    def setUp(self):
        self.admin = _admin()
        self.web = _bearer(self.admin)
        self.client_id = self.register_client()

    def params(self, **over):
        p = {"client_id": self.client_id, "redirect_uri": REDIRECT, "response_type": "code",
             "code_challenge": CHALLENGE, "code_challenge_method": "S256", "scope": oauth.SCOPE, "state": "s1"}
        p.update(over)
        return {k: v for k, v in p.items() if v is not None}

    def check(self, **over):
        return self.web.get(f"{API}/oauth/reporting/check", self.params(**over)).json()

    def test_check_names_the_client_and_resource(self):
        d = self.check()
        self.assertTrue(d["ok"])
        self.assertEqual(d["client"], "Claude")
        self.assertTrue(d["resource"].endswith("/meridian/api/mcp/reporting"))

    def test_an_unknown_client_or_return_address_is_shown_not_redirected(self):
        d = self.check(client_id="nope")
        self.assertFalse(d["ok"])
        self.assertNotIn("redirect", d)
        d = self.check(redirect_uri="https://claude.ai/elsewhere")
        self.assertFalse(d["ok"])
        self.assertNotIn("redirect", d)

    def test_other_errors_go_back_to_the_client(self):
        cases = [({"response_type": "token"}, "unsupported_response_type"),
                 ({"code_challenge_method": "plain"}, "invalid_request"),
                 ({"code_challenge": "short"}, "invalid_request"),
                 ({"scope": "admin:all"}, "invalid_scope"),
                 ({"resource": "https://elsewhere.example.com/mcp"}, "invalid_target")]
        for over, code in cases:
            d = self.check(**over)
            self.assertFalse(d["ok"], over)
            q = parse_qs(urlparse(d["redirect"]).query)
            self.assertEqual(q["error"], [code], over)
            self.assertEqual(q["state"], ["s1"])

    def test_loopback_redirects_ignore_the_port(self):
        cid = self.register_client(["http://127.0.0.1:1111/callback"])
        d = self.check(client_id=cid, redirect_uri="http://127.0.0.1:60000/callback")
        self.assertTrue(d["ok"])
        d = self.check(client_id=cid, redirect_uri="http://127.0.0.1:60000/other")
        self.assertFalse(d["ok"])

    def test_a_single_registered_redirect_is_the_default(self):
        self.assertTrue(self.check(redirect_uri=None)["ok"])

    def test_only_administrators_can_check_approve_or_deny(self):
        for role in (Role.FACULTY, Role.STUDENT):
            u = User.objects.create_user(username=f"u{role}", password="x", role=role)
            c = _bearer(u)
            self.assertEqual(c.get(f"{API}/oauth/reporting/check", self.params()).status_code, 403)
            self.assertEqual(c.post(f"{API}/oauth/reporting/approve", self.params(), format="json").status_code, 403)
            self.assertEqual(c.post(f"{API}/oauth/reporting/deny", self.params(), format="json").status_code, 403)
            self.assertEqual(c.get(f"{API}/admin/reporting").status_code, 403)
        self.assertEqual(APIClient().get(f"{API}/oauth/reporting/check", self.params()).status_code, 401)

    def test_approve_returns_a_code_state_and_issuer(self):
        r = self.web.post(f"{API}/oauth/reporting/approve", self.params(), format="json")
        q = parse_qs(urlparse(r.json()["redirect"]).query)
        self.assertEqual(q["state"], ["s1"])
        self.assertIn("code", q)
        self.assertIn("iss", q)

    def test_approve_refuses_a_bad_request(self):
        r = self.web.post(f"{API}/oauth/reporting/approve", self.params(code_challenge="short"), format="json")
        self.assertEqual(r.status_code, 400)

    def test_deny_goes_back_as_access_denied(self):
        r = self.web.post(f"{API}/oauth/reporting/deny", self.params(), format="json")
        q = parse_qs(urlparse(r.json()["redirect"]).query)
        self.assertEqual(q["error"], ["access_denied"])
        self.assertEqual(q["state"], ["s1"])


class ClientMetadataDocumentTests(ReportingTestCase):
    CID = "https://claude.ai/oauth/mcp-oauth-client-metadata"

    def setUp(self):
        self.web = _bearer(_admin())

    def check(self, cid):
        return self.web.get(f"{API}/oauth/reporting/check", {
            "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
            "code_challenge": CHALLENGE, "code_challenge_method": "S256"}).json()

    def test_claudes_document_is_read_and_cached(self):
        doc = {"client_id": self.CID, "client_name": "Claude", "redirect_uris": [REDIRECT, "https://evil.com/x"]}
        with patch.object(oauth, "fetch_client_document", return_value=doc) as fetch:
            self.assertTrue(self.check(self.CID)["ok"])
            self.assertTrue(self.check(self.CID)["ok"])
        self.assertEqual(fetch.call_count, 1)                        # cached for a day
        from .models import ReportingClient
        row = ReportingClient.objects.get(client_id=self.CID)
        self.assertEqual((row.kind, row.redirect_uris), ("cimd", [REDIRECT]))   # the disallowed URI is dropped

    def test_documents_from_other_hosts_are_never_fetched(self):
        with patch.object(oauth, "fetch_client_document") as fetch:
            self.assertFalse(self.check("https://evil.example.com/meta")["ok"])
            self.assertFalse(self.check("http://claude.ai/meta")["ok"])
        fetch.assert_not_called()

    def test_a_document_that_names_another_client_id_is_refused(self):
        doc = {"client_id": "https://claude.ai/other", "redirect_uris": [REDIRECT]}
        with patch.object(oauth, "fetch_client_document", return_value=doc):
            self.assertFalse(self.check(self.CID)["ok"])

    def test_an_unreachable_document_is_refused(self):
        with patch.object(oauth, "fetch_client_document", side_effect=OSError("down")):
            self.assertFalse(self.check(self.CID)["ok"])


class TokenTests(ReportingTestCase):
    def setUp(self):
        self.admin = _admin()
        self.web = _bearer(self.admin)
        self.client_id = self.register_client()
        self.http = APIClient()

    def code(self):
        r = self.web.post(f"{API}/oauth/reporting/approve", {
            "client_id": self.client_id, "redirect_uri": REDIRECT, "response_type": "code",
            "code_challenge": CHALLENGE, "code_challenge_method": "S256"}, format="json")
        return parse_qs(urlparse(r.json()["redirect"]).query)["code"][0]

    def exchange(self, code, **over):
        body = {"grant_type": "authorization_code", "client_id": self.client_id, "code": code,
                "redirect_uri": REDIRECT, "code_verifier": VERIFIER, **over}
        return self.http.post(f"{API}/oauth/reporting/token", body)

    def test_exchange_gives_tokens_with_the_contract_shape(self):
        r = self.exchange(self.code())
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["access_token"].startswith("flxa_"))
        self.assertTrue(d["refresh_token"].startswith("flxr_"))
        self.assertEqual((d["token_type"], d["expires_in"], d["scope"]), ("Bearer", 3600, "reporting:read"))
        self.assertEqual((r["Cache-Control"], r["Pragma"]), ("no-store", "no-cache"))

    def test_json_bodies_work_too(self):
        body = {"grant_type": "authorization_code", "client_id": self.client_id, "code": self.code(),
                "code_verifier": VERIFIER}
        self.assertEqual(self.http.post(f"{API}/oauth/reporting/token", body, format="json").status_code, 200)

    def test_codes_and_tokens_are_stored_only_as_hashes(self):
        code = self.code()
        d = self.exchange(code).json()
        from .models import ReportingCode
        stored = [ReportingCode.objects.get().code_hash, *ReportingConnection.objects.values_list("access_hash", "refresh_hash")[0]]
        for plain in (code, d["access_token"], d["refresh_token"]):
            self.assertNotIn(plain, stored)
        self.assertEqual(ReportingConnection.objects.get().access_hash, oauth.digest(d["access_token"]))

    def test_a_code_works_once(self):
        code = self.code()
        self.assertEqual(self.exchange(code).status_code, 200)
        r = self.exchange(code)
        self.assertEqual((r.status_code, r.json()["error"]), (400, "invalid_grant"))

    def test_an_expired_code_is_refused(self):
        code = self.code()
        from .models import ReportingCode
        ReportingCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.exchange(code).json()["error"], "invalid_grant")

    def test_a_wrong_verifier_redirect_or_client_is_refused(self):
        for over in ({"code_verifier": "w" * 64}, {"code_verifier": "short"}, {"redirect_uri": "https://claude.ai/other"},
                     {"client_id": self.register_client()}):
            r = self.exchange(self.code(), **over)
            self.assertEqual((r.status_code, r.json()["error"]), (400, "invalid_grant"), over)

    def test_an_unknown_code_and_grant_type(self):
        self.assertEqual(self.exchange("nope").json()["error"], "invalid_grant")
        self.assertEqual(self.http.post(f"{API}/oauth/reporting/token", {"grant_type": "password"}).json()["error"],
                         "unsupported_grant_type")

    def test_the_admin_must_still_be_an_admin_at_exchange(self):
        code = self.code()
        User.objects.filter(pk=self.admin.pk).update(role=Role.FACULTY)
        self.assertEqual(self.exchange(code).json()["error"], "invalid_grant")

    def test_refresh_rotates_and_the_old_token_dies(self):
        first = self.exchange(self.code()).json()
        body = {"grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": first["refresh_token"]}
        r = self.http.post(f"{API}/oauth/reporting/token", body)
        self.assertEqual(r.status_code, 200)
        second = r.json()
        self.assertNotEqual(second["refresh_token"], first["refresh_token"])
        self.assertEqual(self.http.post(f"{API}/oauth/reporting/token", body).json()["error"], "invalid_grant")
        # the old access token stopped with the rotation, the new one works
        mcp = lambda tok: self.http.post(f"{API}/mcp/reporting", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                         format="json", HTTP_AUTHORIZATION=f"Bearer {tok}").status_code
        self.assertEqual(mcp(first["access_token"]), 401)
        self.assertEqual(mcp(second["access_token"]), 200)

    def test_a_refresh_token_belongs_to_one_client(self):
        first = self.exchange(self.code()).json()
        other = self.register_client()
        r = self.http.post(f"{API}/oauth/reporting/token", {
            "grant_type": "refresh_token", "client_id": other, "refresh_token": first["refresh_token"]})
        self.assertEqual(r.json()["error"], "invalid_grant")

    def test_an_expired_refresh_token_is_refused(self):
        first = self.exchange(self.code()).json()
        ReportingConnection.objects.update(refresh_expires_at=timezone.now() - timedelta(seconds=1))
        r = self.http.post(f"{API}/oauth/reporting/token", {
            "grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": first["refresh_token"]})
        self.assertEqual(r.json()["error"], "invalid_grant")

    def test_lifetimes(self):
        self.exchange(self.code())
        c = ReportingConnection.objects.get()
        self.assertAlmostEqual((c.access_expires_at - timezone.now()).total_seconds(), 3600, delta=30)
        self.assertAlmostEqual((c.refresh_expires_at - timezone.now()).total_seconds(), 30 * 86400, delta=30)


@override_settings(ALLOWED_HOSTS=["*"])
class McpTests(ReportingTestCase):
    def setUp(self):
        self.c = Connected(self)

    def test_no_token_is_a_401_with_the_discovery_challenge(self):
        r = APIClient().post(f"{API}/mcp/reporting", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, format="json",
                             HTTP_HOST="api.example.com", secure=True)
        self.assertEqual(r.status_code, 401)
        h = r["WWW-Authenticate"]
        self.assertIn('resource_metadata="https://api.example.com/.well-known/oauth-protected-resource/meridian/api/mcp/reporting"', h)
        self.assertIn('scope="reporting:read"', h)
        self.assertNotIn("invalid_token", h)
        bad = APIClient().post(f"{API}/mcp/reporting", {}, format="json", HTTP_AUTHORIZATION="Bearer flxa_wrong")
        self.assertIn('error="invalid_token"', bad["WWW-Authenticate"])

    def test_get_is_405(self):
        r = APIClient().get(f"{API}/mcp/reporting")
        self.assertEqual((r.status_code, r["Allow"]), (405, "POST"))

    def test_initialize_negotiates_the_protocol_version(self):
        r = self.c.rpc("initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertEqual(self.c.rpc("initialize", {"protocolVersion": "1999-01-01"}).json()["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(r["capabilities"], {"tools": {"listChanged": False}})
        self.assertIn("v1", r["serverInfo"]["version"])

    def test_ping_and_a_notification(self):
        self.assertEqual(self.c.rpc("ping").json()["result"], {})
        r = self.c.http.post(f"{API}/mcp/reporting", {"jsonrpc": "2.0", "method": "notifications/initialized"},
                             format="json", HTTP_AUTHORIZATION=f"Bearer {self.c.tokens['access_token']}")
        self.assertEqual(r.status_code, 202)

    def test_tools_list_has_the_six_read_only_tools(self):
        tools = self.c.rpc("tools/list").json()["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["report_courses", "report_quarters", "report_closes",
                                                      "report_requests", "report_ai_use", "report_health"])
        for t in tools:
            self.assertTrue(t["annotations"]["readOnlyHint"])
            self.assertEqual(t["inputSchema"], {"type": "object", "properties": {}, "additionalProperties": False})
            self.assertIn("sim: ", t["description"])

    def test_bad_requests(self):
        self.assertEqual(self.c.rpc("nope").json()["error"]["code"], -32601)
        self.assertEqual(self.c.rpc("tools/call", {"name": "drop_tables"}).json()["error"]["code"], -32602)
        h = {"HTTP_AUTHORIZATION": f"Bearer {self.c.tokens['access_token']}"}
        self.assertEqual(self.c.http.post(f"{API}/mcp/reporting", "[1]", content_type="application/json", **h).status_code, 400)
        self.assertEqual(self.c.http.post(f"{API}/mcp/reporting", "not json", content_type="application/json", **h).status_code, 400)
        self.assertEqual(self.c.rpc("ping", id_=1).json().get("error"), None)
        bad = self.c.http.post(f"{API}/mcp/reporting", {"id": 1, "method": "ping"}, format="json", **h).json()
        self.assertEqual(bad["error"]["code"], -32600)

    def test_a_tool_call_is_logged_without_its_rows(self):
        _game()
        rows = self.c.call("report_courses")
        call = ReportingCall.objects.get()
        self.assertEqual((call.tool, call.status, call.rows), ("report_courses", "ok", len(rows)))
        self.assertGreaterEqual(call.ms, 0)
        self.assertFalse(hasattr(call, "result"))

    def test_a_failing_report_is_an_error_result_and_logged(self):
        with patch.object(reports, "run", side_effect=RuntimeError("boom")):
            r = self.c.rpc("tools/call", {"name": "report_courses"}).json()["result"]
        self.assertTrue(r["isError"])
        self.assertNotIn("boom", json.dumps(r))
        self.assertEqual(ReportingCall.objects.get().status, "error")

    def test_the_rate_limit(self):
        views._window.clear()
        try:
            with patch.object(views, "CALLS_PER_MINUTE", 3):
                codes = [self.c.rpc("ping").status_code for _ in range(5)]
            self.assertEqual(codes, [200, 200, 200, 429, 429])
        finally:
            views._window.clear()

    def test_revoking_stops_the_connection_at_once(self):
        _bearer(self.c.admin)
        conn_id = ReportingConnection.objects.get().id
        r = self.c.web.post(f"{API}/admin/reporting/connections/{conn_id}/revoke")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["connections"][0]["active"])
        self.assertEqual(self.c.rpc("ping").status_code, 401)

    def test_a_connection_dies_with_its_administrator(self):
        self.assertEqual(self.c.rpc("ping").status_code, 200)
        User.objects.filter(pk=self.c.admin.pk).update(is_active=False)
        self.assertEqual(self.c.rpc("ping").status_code, 401)
        User.objects.filter(pk=self.c.admin.pk).update(is_active=True, role=Role.FACULTY)
        self.assertEqual(self.c.rpc("ping").status_code, 401)

    def test_an_expired_access_token_is_refused(self):
        ReportingConnection.objects.update(access_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.c.rpc("ping").status_code, 401)

    def test_the_admin_screen(self):
        self.c.rpc("ping")
        self.c.call("report_health")
        d = self.c.web.get(f"{API}/admin/reporting").json()
        self.assertTrue(d["connector_url"].endswith("/meridian/api/mcp/reporting"))
        row = d["connections"][0]
        self.assertEqual((row["admin"], row["client"], row["active"], row["calls_7d"]), ("boss@example.com", "Claude", True, 1))
        self.assertIsNotNone(row["last_used_at"])


class ViewTests(ReportingTestCase):
    def setUp(self):
        self.c = Connected(self)

    def conforms(self, tool):
        rows = self.c.call(tool)
        for row in rows:
            self.assertEqual(list(row), list(reports.TOOLS[tool]["columns"]), tool)
            self.assertEqual(next(iter(row)), "sim")
            self.assertEqual(row["sim"], "marketing_management")
        return rows

    def test_every_row_has_exactly_the_declared_columns(self):
        g = _game()
        models.Team.objects.create(game=g, name="Team A")
        models.AiCall.objects.create(game_id_ref=g.id, course=g.name, feature="ask_claude", cost_usd=0.5)
        models.RoundCloseLog.objects.create(game_id_ref=g.id, course=g.name, round=1, source="faculty", status="succeeded",
                                            started_at=timezone.now(), completed_at=timezone.now())
        for tool in reports.TOOLS:
            self.conforms(tool)

    def test_courses(self):
        g = _game()
        t = models.Team.objects.create(game=g, name="Team A")
        real = User.objects.create_user(username="s1", password="x", role=Role.STUDENT, email="s1@example.com")
        test = User.objects.create_user(username="s2", password="x", role=Role.STUDENT, email="test-3@mailinator.com")
        flagged = User.objects.create_user(username="s3", password="x", role=Role.STUDENT, email="s3@example.com")
        for u, is_test in ((real, False), (test, False), (flagged, True)):
            t.users.add(u)
            models.Enrollment.objects.create(game=g, user=u, is_test=is_test)
        draft = _game("Draft")
        models.Game.objects.filter(pk=draft.pk).update(student_deployed=False, status=models.GameStatus.SETUP)
        done = _game("Done")
        models.Game.objects.filter(pk=done.pk).update(status=models.GameStatus.COMPLETE)
        rows = {r["course"]: r for r in self.conforms("report_courses")}
        row = rows["Torque 101"]
        self.assertEqual((row["status"], row["quarter_now"], row["quarters"], row["firms"]), ("running", 2, 14, 1))
        self.assertEqual((row["students"], row["students_confirmed"]), (1, 1))      # no test accounts
        self.assertEqual(row["product"], "Flexee Marketing Management")
        self.assertEqual(row["engine_version"], "1")
        self.assertIsNone(row["institution"])
        self.assertTrue(row["next_deadline_at"].endswith("Z"))
        self.assertEqual(rows["Draft"]["status"], "setting_up")
        self.assertIsNone(rows["Draft"]["quarter_now"])
        self.assertEqual(rows["Done"]["status"], "finished")
        self.assertIsNone(rows["Done"]["next_deadline_at"])

    def test_practice_games_are_left_out_of_every_view(self):
        g = _game()
        practice = _game("Torque 101 (practice)", is_practice=True)
        models.AiCall.objects.create(game_id_ref=practice.id, course=practice.name, feature="focus_group", practice=True)
        models.RoundCloseLog.objects.create(game_id_ref=practice.id, course=practice.name, round=4, source="faculty",
                                            status="failed", started_at=timezone.now(), completed_at=timezone.now(),
                                            practice=True)
        self.assertEqual([r["course"] for r in self.conforms("report_courses")], [g.name])
        self.assertEqual({r["course"] for r in self.conforms("report_quarters")}, {g.name})
        self.assertEqual(self.conforms("report_closes"), [])
        self.assertEqual(self.conforms("report_ai_use"), [])
        self.assertEqual(self.c.call("report_health")[0]["closes_failed"], 0)
        self.assertEqual(self.c.call("report_health")[0]["courses_running"], 1)

    def test_quarters(self):
        g = _game(first_due="2099-03-10")
        models.Team.objects.create(game=g, name="Team A")
        models.Team.objects.create(game=g, name="Team B")
        models.Game.objects.filter(pk=g.pk).update(current_round=5)
        sched = g.schedule
        sched[5]["extended"] = True
        models.Game.objects.filter(pk=g.pk).update(schedule=sched)
        team = g.teams.get(name="Team A")
        models.RoundSubmission.objects.create(game=g, round=5, team=team, submitted_levers={})
        models.RoundCloseLog.objects.create(
            game_id_ref=g.id, course=g.name, round=4, source="deadline", status="succeeded",
            started_at=timezone.now() - timedelta(minutes=3), completed_at=timezone.now(), seconds=120,
            firms=2, firms_submitted=1, firms_carried_forward=1, opened_at=timezone.now() - timedelta(days=7))
        rows = self.conforms("report_quarters")
        self.assertEqual(len(rows), 14)
        by = {r["quarter"]: r for r in rows}
        self.assertEqual([by[q]["warm_up"] for q in (1, 3, 4)], [True, True, False])
        self.assertEqual([by[q]["state"] for q in (3, 4, 5, 6)], ["closed", "closed", "open", "locked"])
        self.assertEqual((by[4]["firms_submitted"], by[4]["firms_carried_forward"], by[4]["close_minutes"]), (1, 1, 2))
        self.assertIsNotNone(by[4]["closed_at"])
        self.assertEqual(by[5]["firms_submitted"], 1)                  # counted live
        self.assertEqual(by[5]["firms_carried_forward"], 0)
        self.assertIsNone(by[6]["firms_submitted"])
        self.assertTrue(by[6]["deadline_extended"])
        self.assertFalse(by[5]["deadline_extended"])
        self.assertEqual(by[5]["deadline_at"], by[5]["deadline_at"])
        self.assertTrue(all(r["opened_after_deadline"] is False and r["explanations_required"] is False for r in rows))
        self.assertIsNone(by[5]["explanations_submitted"])

    def test_overdue_is_ten_minutes_past_the_deadline_and_never_the_last_round(self):
        g = _game(first_due="2020-01-10")                              # long past
        models.Game.objects.filter(pk=g.pk).update(current_round=2)
        self.assertTrue({r["quarter"]: r for r in self.conforms("report_quarters")}[2]["overdue"])
        self.assertEqual(self.c.call("report_health")[0]["quarters_overdue"], 1)
        models.Game.objects.filter(pk=g.pk).update(current_round=14)
        self.assertFalse({r["quarter"]: r for r in self.conforms("report_quarters")}[14]["overdue"])
        self.assertEqual(self.c.call("report_health")[0]["quarters_overdue"], 0)

    def test_a_round_being_closed_is_processing(self):
        g = _game()
        models.Game.objects.filter(pk=g.pk).update(close_claimed_at=timezone.now())
        self.assertEqual({r["quarter"]: r for r in self.conforms("report_quarters")}[2]["state"], "processing")

    def test_closes_in_the_last_90_days(self):
        g = _game()
        now = timezone.now()
        keep = models.RoundCloseLog.objects.create(
            game_id_ref=g.id, course=g.name, round=4, source="faculty", status="succeeded",
            started_at=now, completed_at=now, seconds=1.234)
        models.RoundCloseLog.objects.create(
            game_id_ref=g.id, course=g.name, round=3, source="deadline", status="failed", error_code="other",
            started_at=now - timedelta(days=91), completed_at=now - timedelta(days=91))
        rows = self.conforms("report_closes")
        self.assertEqual([r["close_id"] for r in rows], [keep.id])
        self.assertEqual((rows[0]["quarter"], rows[0]["firm_id"], rows[0]["source"], rows[0]["seconds"]), (4, None, "faculty", 1.2))

    def test_closes_survive_the_game_being_deleted(self):
        g = _game()
        models.RoundCloseLog.objects.create(game_id_ref=g.id, course=g.name, round=4, source="faculty", status="succeeded",
                                            started_at=timezone.now(), completed_at=timezone.now())
        g.delete()
        rows = self.conforms("report_closes")
        self.assertEqual((len(rows), rows[0]["course"]), (1, "Torque 101"))

    def test_requests_are_always_empty(self):
        self.assertEqual(self.c.call("report_requests"), [])

    def test_ai_use_groups_by_course_month_and_feature(self):
        g = _game()
        for outcome, dropped in (("ok", 1), ("error", 0), ("ok", 2)):
            models.AiCall.objects.create(game_id_ref=g.id, course=g.name, feature="ask_claude", outcome=outcome,
                                         sentences_dropped=dropped, input_tokens=1000, output_tokens=100, cost_usd=0.004)
        models.AiCall.objects.create(game_id_ref=g.id, course=g.name, feature="advisors", cost_usd=0.1)
        old = models.AiCall.objects.create(game_id_ref=g.id, course=g.name, feature="advisors", cost_usd=9)
        models.AiCall.objects.filter(pk=old.pk).update(created_at=datetime(2020, 1, 5, tzinfo=dt_timezone.utc))
        rows = self.conforms("report_ai_use")
        by = {r["feature"]: r for r in rows}
        self.assertEqual(len(rows), 2)                                  # the 2020 call is out of the 12 months
        ask = by["ask_claude"]
        self.assertEqual((ask["calls"], ask["failures"], ask["sentences_dropped"], ask["input_tokens"]), (3, 1, 3, 3000))
        self.assertEqual(ask["cost_usd"], 0.01)
        self.assertEqual(ask["month"], timezone.now().strftime("%Y-%m"))

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"})
    def test_health(self):
        models.SchedulerState.objects.create(key=SWEEP_KEY, last_run_at=timezone.now() - timedelta(minutes=2))
        g = _game()
        models.AiCall.objects.create(game_id_ref=g.id, course=g.name, feature="ask_claude", cost_usd=1.5)
        models.RoundCloseLog.objects.create(game_id_ref=g.id, course=g.name, round=4, source="deadline", status="failed",
                                            error_code="other", started_at=timezone.now(), completed_at=timezone.now())
        [h] = self.conforms("report_health")
        self.assertEqual(h["contract"], "v1")
        self.assertTrue(h["scheduler_ok"])
        self.assertTrue(h["scheduler_last_run_at"].endswith("Z"))
        self.assertEqual((h["courses_running"], h["closes_failed"], h["requests_waiting"]), (1, 1, 0))
        self.assertEqual((h["ai_enabled"], h["ai_spent_this_month_usd"], h["ai_ceiling_usd"]), (True, 1.5, 50.0))
        self.assertEqual(h["connections_active"], 1)
        self.assertEqual(h["migrations_pending"], 0)
        from django.db import connection
        self.assertTrue(h["database_version"].startswith(
            "PostgreSQL" if connection.vendor == "postgresql" else "SQLite"))
        for key in ("php_version", "jobs_waiting", "oldest_job_minutes", "failed_jobs_7d", "certificate_expires_at",
                    "certificate_days_left"):
            self.assertIsNone(h[key], key)

    def test_a_stale_or_missing_scheduler_is_not_ok(self):
        self.assertFalse(self.c.call("report_health")[0]["scheduler_ok"])
        models.SchedulerState.objects.create(key=SWEEP_KEY, last_run_at=timezone.now() - timedelta(minutes=11))
        self.assertFalse(self.c.call("report_health")[0]["scheduler_ok"])

    @override_settings(GIT_COMMIT="abc1234")
    def test_the_deployed_commit(self):
        self.assertEqual(self.c.call("report_health")[0]["commit"], "abc1234")

    def test_no_name_or_email_of_a_person_appears_anywhere(self):
        g = _game()
        t = models.Team.objects.create(game=g, name="Team A")
        u = User.objects.create_user(username="zelda_secret", password="x", role=Role.STUDENT,
                                     email="zelda@private.example", first_name="Zelda", last_name="Fitzgerald")
        t.users.add(u)
        models.Enrollment.objects.create(game=g, user=u)
        models.AiCall.objects.create(game_id_ref=g.id, course=g.name, feature="ask_claude")
        everything = json.dumps({tool: self.c.call(tool) for tool in reports.TOOLS})
        for secret in ("zelda", "Zelda", "Fitzgerald", "private.example", "Team A", "@example.com", "boss"):
            self.assertNotIn(secret, everything, secret)
