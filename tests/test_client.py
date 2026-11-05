"""OAuth/transport safety tests. Every identity and credential here is synthetic."""

import base64
import contextlib
import copy
import ctypes
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request
import uuid


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "elsewhere.py"
spec = importlib.util.spec_from_file_location("elsewhere_client", SOURCE)
client_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = client_module
spec.loader.exec_module(client_module)
C = client_module
PROFILE = C.PROFILES["staging"]
NOW = 2000000000


def encoded(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()


def token_response(sequence=1, profile=PROFILE):
    return {"token_type": "Bearer", "scope": C.SCOPES, "expires_in": 600,
        "access_token": encoded({"alg": "synthetic"}) + "." + encoded({
            "iss": profile.issuer, "aud": profile.resource, "exp": NOW + 600,
            "synthetic_sequence": sequence}) + ".synthetic-signature",
        "refresh_token": "synthetic-refresh-value-" + str(sequence)}


def record(sequence=1):
    response = token_response(sequence)
    return {"schema": 1, "state": "ready", "resource": PROFILE.resource,
        "issuer": PROFILE.issuer, "client_id": "00000000-0000-4000-8000-000000000001",
        "access_token": response["access_token"], "refresh_token": response["refresh_token"],
        "expires_at": NOW + 600}


class MemoryStore:
    name = "synthetic-memory-test-only"

    def __init__(self, value=None):
        self.value = copy.deepcopy(value)
        self.history = []
        self.error = None
        self.fail_ready_after_pending = False

    def prepare(self):
        if self.error:
            raise C.ClientError(self.error)

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        if self.fail_ready_after_pending and value["state"] == "ready" and self.value["state"] == "refresh_pending":
            raise C.ClientError("store_locked")
        self.value = copy.deepcopy(value)
        self.history.append(value["state"])

    def delete(self):
        self.value = None


class SyntheticTransport:
    def __init__(self, store):
        self.store = store
        self.requests = []
        self.registrations = self.refreshes = self.code_exchanges = 0
        self.calls = []
        self.sequence = 1
        self.registration = None
        self.auth_parameters = None
        self.resource_patch = self.server_patch = self.registration_patch = None
        self.rpc_override = None
        self.refresh_error = None
        self.token_patch = None

    @staticmethod
    def response(value, status=200, headers=None):
        return C.HttpResponse(status, {"Content-Type": "application/json", **(headers or {})}, json.dumps(value).encode())

    def request(self, method, url, *, headers=None, body=None):
        self.requests.append((method, url))
        if url == PROFILE.resource_metadata:
            value = {"resource": PROFILE.resource, "authorization_servers": [PROFILE.issuer]}
            value.update(self.resource_patch or {})
            return self.response(value)
        if url == PROFILE.server_metadata:
            value = {"issuer": PROFILE.issuer, "authorization_endpoint": PROFILE.issuer + "/oauth/authorize",
                "token_endpoint": PROFILE.issuer + "/oauth/token", "registration_endpoint": PROFILE.issuer + "/oauth/register",
                "authorization_response_iss_parameter_supported": True, "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"], "grant_types_supported": ["authorization_code", "refresh_token"],
                "scopes_supported": C.SCOPES.split()}
            value.update(self.server_patch or {})
            return self.response(value)
        if url == PROFILE.issuer + "/oauth/register":
            self.registrations += 1
            self.registration = json.loads(body)
            value = dict(self.registration, client_id="00000000-0000-4000-8000-000000000001")
            value.update(self.registration_patch or {})
            return self.response(value, 201)
        if url == PROFILE.issuer + "/oauth/token":
            values = {key: items[0] for key, items in urllib.parse.parse_qs(body.decode()).items()}
            if values["resource"] != PROFILE.resource:
                raise AssertionError("resource binding missing")
            if values["grant_type"] == "refresh_token":
                self.refreshes += 1
                if self.store.value["state"] != "refresh_pending":
                    raise AssertionError("rotation attempted without durable pending marker")
                if self.refresh_error:
                    raise C.ClientError(self.refresh_error)
            else:
                self.code_exchanges += 1
                challenge = base64.urlsafe_b64encode(hashlib.sha256(values["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                if (self.auth_parameters["code_challenge"] != challenge
                    or self.auth_parameters["code_challenge_method"] != "S256"
                    or values["redirect_uri"] != self.registration["redirect_uris"][0]
                    or values["client_id"] != self.auth_parameters["client_id"]):
                    raise AssertionError("PKCE/redirect/client binding missing")
            self.sequence += 1
            response = token_response(self.sequence)
            response.update(self.token_patch or {})
            return self.response(response)
        if url == PROFILE.resource:
            envelope = json.loads(body)
            if self.rpc_override:
                override = self.rpc_override(envelope)
                if override is not None:
                    return override
            method = envelope["method"]
            if method == "notifications/initialized":
                return C.HttpResponse(202, {}, b"")
            if self.store.value["state"] != "ready":
                raise AssertionError("data attempted before durable token save")
            if headers["Authorization"] != "Bearer " + self.store.value["access_token"]:
                raise AssertionError("stale access token")
            if method == "initialize":
                result = {"protocolVersion": C.PROTOCOLS[0], "capabilities": {"tools": {}}, "serverInfo": {"name": "synthetic"}}
            elif method == "tools/list":
                result = {"tools": [{"name": name} for name in sorted(C.TOOLS)]}
            else:
                name = envelope["params"]["name"]
                self.calls.append(name)
                result = {"content": [{"type": "text", "text": "synthetic result"}],
                    "structuredContent": {"relation_keys": []}, "_meta": {"elsewhere/usageUnits": 0}}
            return self.response({"jsonrpc": "2.0", "id": envelope["id"], "result": result})
        raise AssertionError("unexpected outbound URL")

    def browser(self, url):
        values = {key: value[0] for key, value in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        self.auth_parameters = values
        if values["resource"] != PROFILE.resource or values["scope"] != C.SCOPES:
            raise AssertionError("authorization binding missing")
        callback = values["redirect_uri"] + "?" + urllib.parse.urlencode({
            "state": values["state"], "iss": PROFILE.issuer, "code": "synthetic-authorization-code"})
        with urllib.request.urlopen(callback, timeout=2) as response:
            if response.status != 200:
                raise AssertionError("callback rejected")
        return True


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = MemoryStore()
        self.transport = SyntheticTransport(self.store)
        self.events = []
        self.client = self.new_client()

    def new_client(self):
        return C.SessionClient(PROFILE, store=self.store, transport=self.transport,
            state_dir=self.temp.name, browser_opener=self.transport.browser, emit=self.events.append,
            now=lambda: NOW, authorization_timeout=1)

    def assert_error(self, code, fn):
        with self.assertRaises(C.ClientError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_fresh_connect_and_new_client_instance_reuse_one_identity(self):
        self.assertEqual(self.client.connect()["status"], "connected")
        self.assertEqual(self.transport.registrations, 1)
        self.assertEqual(self.transport.code_exchanges, 1)
        self.assertEqual(self.store.history, ["ready"])
        self.assertEqual(self.transport.calls, ["list_relation_keys"])
        self.assertEqual(self.events[0]["event"], "authorization_required")
        self.assertTrue(self.events[0]["browser_opened"])
        self.assertNotIn("code_verifier", self.events[0]["authorization_url"])
        self.assertEqual(len(self.new_client().list_tools()), 19)
        self.assertEqual(self.new_client().connect()["status"], "connected")
        self.assertEqual(self.transport.registrations, 1)
        self.assertEqual(len(self.events), 1)
        self.assertFalse(list(Path(self.temp.name).rglob("*.json")))

    def test_missing_noninteractive_does_not_start_network_or_browser(self):
        self.assert_error("reauth_required", lambda: self.client.call("list_relation_keys", {}, True))
        self.assertEqual(self.client.status()["status"], "disconnected")
        self.assertEqual(self.transport.requests, [])
        self.assertEqual(self.events, [])

    def test_front_call_without_session_connects_without_host_config(self):
        result = self.client.call("list_relation_keys", {})
        self.assertFalse(result.get("isError", False))
        self.assertEqual(self.transport.registrations, 1)

    def test_saved_pending_and_reauth_stop_background_but_foreground_connect_recovers(self):
        for state in ("refresh_pending", "reauth_required"):
            with self.subTest(state=state):
                self.store.value = dict(record(), state=state)
                before = self.transport.registrations
                self.assert_error("reauth_required", lambda: self.new_client().call("list_relation_keys", {}, True))
                self.assert_error("reauth_required", lambda: self.new_client().status(check=True))
                self.assertEqual(self.transport.registrations, before)
                self.assertEqual(self.new_client().connect()["status"], "connected")
                self.assertEqual(self.transport.registrations, before + 1)

    def test_normal_refresh_journals_then_saves_before_data_and_reuses(self):
        self.store.value = dict(record(), expires_at=NOW - 1)
        old = self.store.value["refresh_token"]
        self.client.call("list_relation_keys", {}, True)
        self.assertEqual(self.store.history, ["refresh_pending", "ready"])
        self.assertNotEqual(self.store.value["refresh_token"], old)
        self.new_client().call("list_relation_keys", {}, True)
        self.assertEqual(self.transport.refreshes, 1)
        self.assertEqual(self.transport.registrations, 0)

    def test_lost_rotation_response_survives_restart_without_reusing_refresh(self):
        self.store.value = dict(record(), expires_at=NOW - 1)
        self.transport.refresh_error = "network_error"
        self.assert_error("reauth_required", lambda: self.client.call("list_relation_keys", {}, True))
        self.assertEqual(self.store.value["state"], "refresh_pending")
        self.assert_error("reauth_required", lambda: self.new_client().call("list_relation_keys", {}, True))
        self.assertEqual(self.transport.refreshes, 1)
        self.assertEqual(self.transport.calls, [])

    def test_failed_new_record_save_keeps_pending_and_does_not_deliver_data(self):
        self.store.value = dict(record(), expires_at=NOW - 1)
        self.store.fail_ready_after_pending = True
        self.assert_error("reauth_required", lambda: self.client.call("list_relation_keys", {}, True))
        self.assert_error("reauth_required", lambda: self.new_client().call("list_relation_keys", {}, True))
        self.assertEqual(self.store.value["state"], "refresh_pending")
        self.assertEqual(self.transport.refreshes, 1)
        self.assertEqual(self.transport.calls, [])

    def test_pending_save_failure_prevents_rotation(self):
        self.store.value = dict(record(), expires_at=NOW - 1)
        with patch.object(self.store, "save", side_effect=C.ClientError("store_locked")):
            self.assert_error("store_locked", lambda: self.client.call("list_relation_keys", {}, True))
        self.assertEqual(self.transport.requests, [])
        self.assertEqual(self.store.value["state"], "ready")

    def test_store_failure_precedes_oauth_dcr_and_never_falls_back(self):
        for code in ("store_locked", "store_unavailable"):
            self.store.error = code
            self.assert_error(code, lambda: self.client.connect())
            capabilities = self.client.capabilities()
            self.assertFalse(capabilities["secure_store_available"])
            self.assertEqual(capabilities["code"], code)
        self.assertEqual(self.transport.requests, [])
        self.assertEqual(self.events, [])

    def test_discovery_rejects_new_origin_and_wrong_resource_before_dcr(self):
        for kind, value in (("resource_patch", {"resource": "https://evil.invalid/mcp"}),
                            ("resource_patch", {"authorization_servers": ["https://evil.invalid"]}),
                            ("server_patch", {"token_endpoint": "https://evil.invalid/token"}),
                            ("server_patch", {"authorization_response_iss_parameter_supported": False})):
            with self.subTest(kind=kind):
                self.transport.resource_patch = self.transport.server_patch = None
                setattr(self.transport, kind, value)
                self.assert_error("metadata_mismatch", self.client.connect)
        self.assertEqual(self.transport.registrations, 0)
        self.assertEqual(self.events, [])

    def test_dcr_response_must_bind_redirect_scopes_and_public_client(self):
        for change in ({"redirect_uris": ["https://evil.invalid/callback"]}, {"scope": "other"},
                       {"client_secret": "synthetic-not-allowed"}, {"response_types": ["token"]}):
            with self.subTest(field=next(iter(change))):
                self.transport.registration_patch = change
                self.assert_error("invalid_registration_response", self.client.connect)
        self.assertEqual(self.transport.code_exchanges, 0)
        self.assertEqual(self.events, [])

    def test_cached_token_binding_and_nonfinite_expiry_fail_closed(self):
        for change in ({"expires_at": float("nan")}, {"expires_at": True},
                       {"access_token": token_response(profile=C.PROFILES["production"])["access_token"]},
                       {"client_id": "not-an-oauth-client"}):
            self.store.value = dict(record(), **change)
            self.assert_error("store_invalid", self.client.connect)
        self.assertEqual(self.transport.requests, [])

    def test_quota_and_entity_errors_preserve_usage_and_retry_after(self):
        self.store.value = record()
        for code in ("quota_exceeded", "entity_coverage_exceeded", "rate_limited"):
            result = {"isError": True, "content": [{"type": "text", "text": "synthetic"}],
                "structuredContent": {"error": {"code": code, "details": {"limit": 3}}},
                "_meta": {"elsewhere/usageUnits": 0}}
            def override(envelope):
                if envelope["method"] == "tools/call":
                    return self.transport.response({"jsonrpc": "2.0", "id": envelope["id"], "result": result},
                        429, {"Retry-After": "3600"})
            self.transport.rpc_override = override
            actual = self.client.call("list_relation_keys", {}, True)
            self.assertEqual(actual["structuredContent"]["error"]["code"], code)
            self.assertEqual(actual["_meta"], {"elsewhere/usageUnits": 0, "elsewhere/retryAfterSeconds": 3600})

    def test_non_rpc_throttle_is_bounded_and_never_exposes_html(self):
        self.store.value = record()
        self.transport.rpc_override = lambda envelope: C.HttpResponse(429, {"Retry-After": "999999"}, b"synthetic hidden html")
        error = self.assert_error("rate_limited", lambda: self.client.call("list_relation_keys", {}, True))
        self.assertEqual(error.retry_after, 86400)
        self.assertEqual(self.store.value["state"], "ready")

    def test_protection_401_403_or_wrong_challenge_does_not_erase_credentials(self):
        for status, headers in ((401, {}), (403, {}), (401, {"WWW-Authenticate": 'Bearer error="invalid_token", resource_metadata="https://evil.invalid"'})):
            self.store.value = record()
            self.transport.rpc_override = lambda envelope: C.HttpResponse(status, headers, b"<html>protection</html>")
            self.assert_error("access_blocked", self.client.connect)
            self.assertEqual(self.store.value["state"], "ready")
            self.assertEqual(self.transport.registrations, 0)

    def test_bound_revocation_stops_background_then_explicit_connect_recovers_once(self):
        self.store.value = record()
        def revoked(envelope):
            if self.transport.registrations == 0:
                return C.HttpResponse(401, {"WWW-Authenticate":
                    'Bearer error="invalid_token", resource_metadata="' + PROFILE.resource_metadata + '"'}, b"{}")
        self.transport.rpc_override = revoked
        self.assert_error("reauth_required", lambda: self.client.call("list_relation_keys", {}, True))
        self.assertEqual(self.store.value["state"], "reauth_required")
        self.assertNotIn("refresh_token", self.store.value)
        self.assertEqual(self.client.connect()["status"], "connected")
        self.assertEqual(self.transport.registrations, 1)

    def test_explicit_connect_recovers_live_revocation_but_not_temporary_errors(self):
        self.store.value = record()
        def revoked(envelope):
            if self.transport.registrations == 0:
                return self.transport.response({"jsonrpc": "2.0", "id": envelope["id"],
                    "error": {"code": -32001, "data": {"code": "reauth_required"}}}, 401)
        self.transport.rpc_override = revoked
        self.assertEqual(self.client.connect()["status"], "connected")
        self.assertEqual(self.transport.registrations, 1)
        self.transport.rpc_override = lambda envelope: C.HttpResponse(503, {}, b"unavailable")
        self.assert_error("temporarily_unavailable", self.client.connect)
        self.assertEqual(self.transport.registrations, 1)

    def test_new_authorization_verification_failure_is_not_retried(self):
        self.transport.rpc_override = lambda envelope: C.HttpResponse(401, {
            "WWW-Authenticate": 'Bearer error="invalid_token", resource_metadata="' + PROFILE.resource_metadata + '"'}, b"{}")
        self.assert_error("reauth_required", self.client.connect)
        self.assertEqual(self.transport.registrations, 1)

    def test_server_tool_allowlist_is_checked_before_first_data_call(self):
        self.store.value = record()
        def override(envelope):
            if envelope["method"] == "tools/list":
                return self.transport.response({"jsonrpc": "2.0", "id": envelope["id"],
                    "result": {"tools": [{"name": "unknown"}]}})
        self.transport.rpc_override = override
        self.assert_error("server_contract_mismatch", lambda: self.client.call("list_relation_keys", {}, True))
        self.assertEqual(self.transport.calls, [])

    def test_simultaneous_clients_serialize_one_rotation_and_reuse(self):
        self.store.value = dict(record(), expires_at=NOW - 1)
        errors = []
        def run():
            try:
                self.new_client().call("list_relation_keys", {}, True)
            except Exception as error:
                errors.append(type(error).__name__)
        threads = [threading.Thread(target=run) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        self.assertEqual(errors, [])
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(self.transport.refreshes, 1)
        self.assertEqual(len(self.transport.calls), 3)


class BoundaryTests(unittest.TestCase):
    def test_secret_redaction_in_keys_values_nesting_collision_and_surrogates(self):
        secret = "synthetic-credential-for-redaction"
        value = {secret: {"prefix-" + secret: [secret, {"access_token": "unrecognized-value"}]},
                 "[redacted]": secret, "surrogate": "\ud800"}
        output = json.dumps(C._safe(value, [secret]), ensure_ascii=False).encode("utf-8")
        self.assertNotIn(secret.encode(), output)
        self.assertNotIn(b"unrecognized-value", output)
        self.assertIn(b"[redacted]", output)

    def test_callback_checks_state_issuer_host_duplicates_and_code_error_conflict(self):
        listener = C.CallbackListener("synthetic-state", PROFILE.issuer)
        self.addCleanup(listener.close)
        good = {"state": "synthetic-state", "iss": PROFILE.issuer, "code": "synthetic-authorization-code"}
        cases = [dict(good, state="wrong"), dict(good, iss="https://evil.invalid"),
                 dict(good, error="access_denied"), dict(good, unexpected="value")]
        for values in cases:
            request = listener.redirect_uri + "?" + urllib.parse.urlencode(values)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(error.exception.code, 400)
            self.assertTrue(listener.results.empty())
        for suffix, headers in (("&state=synthetic-state", {}), ("", {"Host": "evil.invalid"})):
            request = urllib.request.Request(listener.redirect_uri + "?" + urllib.parse.urlencode(good) + suffix, headers=headers)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(request, timeout=2)
            self.assertTrue(listener.results.empty())
        with urllib.request.urlopen(listener.redirect_uri + "?" + urllib.parse.urlencode(good), timeout=2) as response:
            body = response.read()
            self.assertNotIn(good["code"].encode(), body)
        self.assertEqual(listener.wait(0.1), good["code"])

    def test_http_transport_rejects_redirect_without_following(self):
        class RedirectOpener:
            def open(self, request, timeout):
                return urllib.error.HTTPError(request.full_url, 302, "redirect", {"Location": "https://evil.invalid"}, io.BytesIO(b""))
        transport = C.HttpTransport()
        transport.opener = RedirectOpener()
        with self.assertRaises(C.ClientError) as error:
            transport.request("GET", PROFILE.resource_metadata)
        self.assertEqual(error.exception.code, "redirect_rejected")

    def test_linux_and_other_platforms_fail_closed_without_prompt_subprocess(self):
        for platform in ("linux", "win32"):
            with patch.object(C.sys, "platform", platform):
                store = C.default_store(PROFILE)
                self.assertIsInstance(store, C.UnavailableStore)
                with self.assertRaises(C.ClientError) as error:
                    store.prepare()
                self.assertEqual(error.exception.code, "store_unavailable")

    def test_cli_flags_work_before_and_after_command_without_endpoint_or_store_escape(self):
        for profile in ("production", "staging"):
            for argv in (("--profile", profile, "--non-interactive", "call", "list_relation_keys"),
                         ("call", "list_relation_keys", "--profile", profile, "--non-interactive")):
                args = C._parser().parse_args(argv)
                self.assertEqual(args.profile, profile)
                self.assertTrue(args.non_interactive)
        defaults = C._parser().parse_args(["status"])
        self.assertEqual(defaults.profile, "production")
        self.assertFalse(defaults.non_interactive)
        for argv in (("connect", "--endpoint", "https://evil.invalid"), ("connect", "--store", "file")):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                C._parser().parse_args(argv)

    def test_unknown_exception_cannot_expose_message(self):
        output = io.StringIO()
        with patch.object(C, "SessionClient", side_effect=RuntimeError("synthetic-secret-that-must-stay-hidden")), contextlib.redirect_stdout(output):
            self.assertEqual(C.main(["status"]), 1)
        self.assertEqual(json.loads(output.getvalue()), {"event": "error", "code": "client_error"})

    def test_json_rejects_duplicates_nonfinite_and_nonobject(self):
        for body in (b'{"a":1,"a":2}', b'{"a":NaN}', b"[]", b"\xff"):
            with self.assertRaises(C.ClientError):
                C._json(body)

    def test_cross_process_lock_has_bounded_busy_and_releases_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            code = ("import importlib.util,sys; "
                "s=importlib.util.spec_from_file_location('lock_client',sys.argv[1]); "
                "m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); "
                "lock=m.ProcessLock(sys.argv[2]); lock.__enter__(); print('ready',flush=True); "
                "sys.stdin.readline(); lock.__exit__()")
            process = subprocess.Popen([sys.executable, "-c", code, str(SOURCE), directory], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "ready")
                with self.assertRaises(C.ClientError) as error:
                    with C.ProcessLock(directory, timeout=0.15):
                        self.fail("concurrent process acquired OAuth lock")
                self.assertEqual(error.exception.code, "client_busy")
            finally:
                process.communicate("release\n", timeout=3)
            self.assertEqual(process.returncode, 0)
            with C.ProcessLock(directory, timeout=0.1):
                self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["authorization.lock"])

    def test_mac_probe_is_deleted_even_when_verification_read_raises(self):
        deleted, released = [], []
        class Security:
            def SecKeychainAddGenericPassword(self, *args):
                args[-1]._obj.value = 991
                return 0
            def SecKeychainItemDelete(self, reference):
                deleted.append(reference.value)
                return 0
        class CF:
            def CFRelease(self, reference):
                released.append(reference.value)
        store = C.MacKeychainStore(service="synthetic-only", account="synthetic")
        store._security, store._cf = Security(), CF()
        with patch.object(store, "_open", return_value=ctypes.c_void_p(990)), patch.object(
            store, "_find", side_effect=C.ClientError("store_locked")):
            with self.assertRaises(C.ClientError):
                store.prepare()
        self.assertEqual(deleted, [991])
        self.assertEqual(sorted(released), [990, 991])


class BrowserOpenerTests(unittest.TestCase):
    url = "https://auth.elsewhere.news/oauth/authorize?state=public-state&code_challenge=public-challenge"

    def test_success_uses_exact_open_argv_without_shell_or_inherited_io(self):
        with patch.object(C.sys, "platform", "darwin"), patch.object(C.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertIs(C._open_default_browser(self.url), True)
        run.assert_called_once_with(["/usr/bin/open", self.url], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        source = SOURCE.read_text()
        self.assertNotIn("webbrowser", source)
        self.assertNotIn("osascript", source)
        self.assertNotIn("AppleScript", source)

    def test_nonzero_returns_false_without_fallback(self):
        with patch.object(C.sys, "platform", "darwin"), patch.object(C.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 1)) as run:
            self.assertIs(C._open_default_browser(self.url), False)
        self.assertEqual(run.call_count, 1)

    def test_timeout_returns_false_without_fallback(self):
        with patch.object(C.sys, "platform", "darwin"), patch.object(C.subprocess, "run",
            side_effect=subprocess.TimeoutExpired("/usr/bin/open", 10)) as run:
            self.assertIs(C._open_default_browser(self.url), False)
        self.assertEqual(run.call_count, 1)

    def test_os_exception_returns_false_without_fallback(self):
        with patch.object(C.sys, "platform", "darwin"), patch.object(C.subprocess, "run",
            side_effect=OSError("synthetic launch error")) as run:
            self.assertIs(C._open_default_browser(self.url), False)
        self.assertEqual(run.call_count, 1)

    def test_unexpected_exception_returns_false_without_fallback(self):
        with patch.object(C.sys, "platform", "darwin"), patch.object(C.subprocess, "run",
            side_effect=RuntimeError("synthetic launch error")) as run:
            self.assertIs(C._open_default_browser(self.url), False)
        self.assertEqual(run.call_count, 1)

    def test_other_platforms_never_launch_a_browser(self):
        for platform in ("linux", "win32", "unknown"):
            with self.subTest(platform=platform), patch.object(C.sys, "platform", platform), patch.object(
                C.subprocess, "run") as run:
                self.assertIs(C._open_default_browser(self.url), False)
            run.assert_not_called()

    def test_session_preserves_injected_opener_and_default_uses_open_helper(self):
        injected = lambda url: True
        self.assertIs(C.SessionClient(browser_opener=injected).browser_opener, injected)
        self.assertIs(C.SessionClient().browser_opener, C._open_default_browser)


if __name__ == "__main__":
    unittest.main()
