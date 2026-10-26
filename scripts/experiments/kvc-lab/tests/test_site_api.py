import json
import tempfile
import types
import unittest
from functools import wraps
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask, g, jsonify, request

from kvclab.site_api import MAX_JSON_BYTES, register_lab


PREFIX = "/site-api/kvc-lab"
JOB_ID = "a" * 32
OWNER = "fixture-yche-owner"
COOKIE_USERS = {
    "owner-session-a": OWNER,
    "owner-session-b": OWNER,
    "other-session": "fixture-other-user",
    "deleted-session": "fixture-deleted-user",
}


def fixture_login_required(view):
    @wraps(view)
    def decorated(*args, **kwargs):
        username = COOKIE_USERS.get(request.cookies.get("auth_token"))
        if username is None:
            return jsonify({"error": "Authentication required"}), 401
        g.username = username
        return view(*args, **kwargs)

    return decorated


class FakeService:
    csrf_secret = b"fixture-secret-for-test-use-only-0123456789"

    def __init__(self, root):
        self.root = root
        self.calls = []
        self.jobs = {}
        self.failure = None

    def catalog(self, user):
        self.calls.append(("catalog", user))
        return {"products": ["datasystem", "mooncake"]}

    def state(self, user):
        if self.failure is not None:
            raise self.failure
        self.calls.append(("state", user))
        return {"phase": "idle"}

    def preview_plan(self, config):
        self.calls.append(("preview_plan", config))
        return {"config": config, "steps": ["deploy", "run", "cleanup"]}

    def create_job(self, user, action, config, idempotency_key):
        self.calls.append(("create_job", user, action, config, idempotency_key))
        if idempotency_key not in self.jobs:
            self.jobs[idempotency_key] = {"id": JOB_ID, "action": action, "status": "queued"}
        return self.jobs[idempotency_key]

    def get_job(self, user, job_id):
        self.calls.append(("get_job", user, job_id))
        if job_id != JOB_ID:
            raise FileNotFoundError()
        return {"id": job_id, "status": "finished"}

    def get_artifact(self, user, job_id, name):
        self.calls.append(("get_artifact", user, job_id, name))
        if job_id != JOB_ID or name != "result.json":
            raise FileNotFoundError()
        return self.root / "result.json", "experiment-result.json"


class SiteApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<main>PRIVATE-CONTROLS</main>")
        (self.web / "app.js").write_text("window.privateControls = true;")
        (self.web / "style.css").write_text(".private-control { color: red; }")
        (self.web / "guide.html").write_text("<main>PRIVATE-GUIDE</main>")
        (self.root / "result.json").write_text('{"valid":true}\n')
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.service = FakeService(self.root)
        register_lab(
            self.app, fixture_login_required, [OWNER, "fixture-deleted-user"],
            self.service, self.web,
            user_exists=lambda user: user in (OWNER, "fixture-other-user"),
        )
        self.client = self.app.test_client()

    def login(self, cookie="owner-session-a"):
        self.client.set_cookie("auth_token", cookie)

    def token(self):
        return self.client.get(PREFIX + "/catalog").get_json()["csrf_token"]

    def headers(self, token=None):
        return {
            "Origin": "https://yche.me",
            "X-CSRF-Token": self.token() if token is None else token,
        }

    def job_body(self):
        return {
            "action": "run", "config": {"workers": 2, "clients": 8},
            "idempotency_key": "fixture-request-001",
        }

    def assert_private(self, response):
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("Cookie", response.vary)
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])

    def test_anonymous_cannot_access_any_service_or_asset(self):
        for path in (
            "/catalog", "/state", "/jobs/" + JOB_ID,
            "/jobs/" + JOB_ID + "/artifacts/result.json",
            "/ui/", "/ui/index.html", "/ui/app.js", "/ui/style.css", "/guide.html", "/source/source.tar.gz", "/source/source.json",
        ):
            with self.subTest(path=path):
                response = self.client.get(PREFIX + path)
                self.assertEqual(401, response.status_code)
                self.assertNotIn(b"PRIVATE-CONTROLS", response.data)
                self.assertNotIn(b"PRIVATE-GUIDE", response.data)
                self.assertNotIn(b"privateControls", response.data)
                self.assert_private(response)
        self.assertEqual([], self.service.calls)

    def test_anonymous_control_page_has_only_generic_login_shell(self):
        response = self.client.get(PREFIX + "/ui/")
        self.assertEqual("text/html", response.mimetype)
        self.assertIn("请先登录".encode(), response.data)
        self.assertIn(b'href="/"', response.data)
        self.assertNotIn(b"mooncake", response.data)

    def test_invalid_session_and_unauthenticated_write_are_rejected(self):
        self.login("expired-or-forged")
        self.assertEqual(401, self.client.get(PREFIX + "/state").status_code)
        self.assertEqual(401, self.client.post(PREFIX + "/jobs", json=self.job_body()).status_code)
        self.assertEqual([], self.service.calls)

    def test_existing_but_non_allowlisted_account_is_forbidden(self):
        self.login("other-session")
        for path in ("/catalog", "/ui/", "/guide.html", "/ui/app.js"):
            self.assertEqual(403, self.client.get(PREFIX + path).status_code)
        self.assertEqual([], self.service.calls)

    def test_allowlisted_but_deleted_account_is_forbidden(self):
        self.login("deleted-session")
        self.assertEqual(403, self.client.get(PREFIX + "/catalog").status_code)
        self.assertEqual([], self.service.calls)

    def test_default_account_lookup_checks_real_users_table_and_closes_connection(self):
        application = Flask("default-database-adapter")
        database = Mock()
        database.execute.return_value.fetchone.return_value = (1,)
        module = types.SimpleNamespace(get_db=lambda: database)
        register_lab(application, fixture_login_required, [OWNER], self.service, self.web)
        client = application.test_client()
        client.set_cookie("auth_token", "owner-session-a")
        with patch.dict("sys.modules", {"database": module}):
            self.assertEqual(200, client.get(PREFIX + "/state").status_code)
            database.execute.assert_called_once_with("SELECT 1 FROM users WHERE username = ?", (OWNER,))
            database.close.assert_called_once_with()
            database.execute.return_value.fetchone.return_value = None
            self.assertEqual(403, client.get(PREFIX + "/state").status_code)

    def test_allowlisted_owner_reads_catalog_state_and_private_ui(self):
        self.login()
        response = self.client.get(PREFIX + "/catalog")
        self.assertEqual(200, response.status_code)
        self.assertEqual(["datasystem", "mooncake"], response.json["products"])
        self.assertRegex(response.json["csrf_token"], r"^\d+\.[0-9a-f]{64}$")
        self.assert_private(response)
        self.assertEqual({"phase": "idle"}, self.client.get(PREFIX + "/state").json)
        for path, expected in (
            ("/ui/", b"PRIVATE-CONTROLS"), ("/ui/app.js", b"privateControls"),
            ("/ui/style.css", b"private-control"), ("/ui/guide.html", b"PRIVATE-GUIDE"),
        ):
            response = self.client.get(PREFIX + path)
            self.assertEqual(200, response.status_code)
            self.assertIn(expected, response.data)
            self.assert_private(response)
            response.close()

    def test_guide_alias_redirects_to_protected_canonical_asset_base(self):
        self.login()
        response = self.client.get(PREFIX + "/guide.html")
        self.assertEqual(302, response.status_code)
        self.assertEqual(PREFIX + "/ui/guide.html", response.headers["Location"])
        self.assert_private(response)
        response = self.client.get(PREFIX + "/guide.html", follow_redirects=True)
        self.assertIn(b"PRIVATE-GUIDE", response.data)
        self.assertEqual(200, response.status_code)
        response.close()

    def test_head_requests_still_require_authentication(self):
        self.assertEqual(401, self.client.head(PREFIX + "/ui/app.js").status_code)
        self.login()
        response = self.client.head(PREFIX + "/ui/app.js")
        self.assertEqual(200, response.status_code)
        self.assertEqual(b"", response.data)
        self.assert_private(response)
        response.close()

    def test_explicit_same_origin_and_csrf_are_required(self):
        self.login()
        good = self.headers()
        attempts = (
            {}, {"Origin": "https://yche.me"},
            {**good, "Origin": "https://evil.example"},
            {**good, "Origin": "https://yche.me.evil.example"},
            {**good, "Origin": "http://yche.me"},
            {**good, "Origin": "https://yche.me/"},
            {**good, "X-CSRF-Token": "wrong"},
            {**good, "X-CSRF-Token": "非ASCII"},
        )
        for headers in attempts:
            with self.subTest(headers=headers):
                response = self.client.post(PREFIX + "/jobs", json=self.job_body(), headers=headers)
                self.assertEqual(403, response.status_code)
                self.assert_private(response)
        self.assertFalse(any(call[0] == "create_job" for call in self.service.calls))

    def test_csrf_is_bound_to_login_session(self):
        self.login()
        token = self.token()
        self.login("owner-session-b")
        response = self.client.post(PREFIX + "/jobs", json=self.job_body(), headers=self.headers(token))
        self.assertEqual(403, response.status_code)

    def test_csrf_accepts_previous_hour_and_rejects_expired_or_future_bucket(self):
        self.login()
        with patch("kvclab.site_api.time.time", return_value=100 * 3600 + 20):
            token = self.token()
        for bucket, status in ((99, 403), (100, 200), (101, 200), (102, 403)):
            with self.subTest(bucket=bucket), patch(
                "kvclab.site_api.time.time", return_value=bucket * 3600 + 30
            ):
                response = self.client.post(
                    PREFIX + "/plans", json={"config": {}}, headers=self.headers(token)
                )
                self.assertEqual(status, response.status_code)

    def test_json_type_encoding_and_size_are_enforced(self):
        self.login()
        headers = self.headers()
        cases = (
            ("{}", "text/plain", {}, 415),
            ("{}", "application/json", {"Content-Encoding": "gzip"}, 415),
            ("{" + "x" * MAX_JSON_BYTES, "application/json", {}, 413),
            ("not-json", "application/json", {}, 400),
            ("[]", "application/json", {}, 400),
        )
        for body, mime, extra, expected in cases:
            with self.subTest(expected=expected, mime=mime):
                response = self.client.post(
                    PREFIX + "/jobs", data=body, content_type=mime, headers={**headers, **extra}
                )
                self.assertEqual(expected, response.status_code)
                self.assert_private(response)

    def test_streaming_body_without_content_length_is_bounded(self):
        import io

        self.login()
        body = b"x" * (MAX_JSON_BYTES + 10)
        response = self.client.post(
            PREFIX + "/jobs", content_type="application/json", headers=self.headers(),
            environ_overrides={"wsgi.input": io.BytesIO(body), "CONTENT_LENGTH": "", "wsgi.input_terminated": True},
        )
        self.assertEqual(413, response.status_code)

    def test_job_submission_forwards_identity_config_and_idempotency(self):
        self.login()
        headers = self.headers()
        first = self.client.post(PREFIX + "/jobs", json=self.job_body(), headers=headers)
        second = self.client.post(PREFIX + "/jobs", json=self.job_body(), headers=headers)
        self.assertEqual(200, first.status_code)
        self.assertEqual(first.json, second.json)
        self.assertEqual(JOB_ID, first.json["job"]["id"])
        calls = [call for call in self.service.calls if call[0] == "create_job"]
        self.assertEqual(2, len(calls))
        self.assertEqual(("create_job", OWNER, "run", {"workers": 2, "clients": 8}, "fixture-request-001"), calls[0])
        self.assertEqual(1, len(self.service.jobs))
        self.assert_private(first)

    def test_plan_forwards_only_config_and_returns_plan_envelope(self):
        self.login()
        response = self.client.post(PREFIX + "/plans", json={"config": {"workers": 4}}, headers=self.headers())
        self.assertEqual(200, response.status_code)
        self.assertEqual({"workers": 4}, response.json["plan"]["config"])
        self.assertIn(("preview_plan", {"workers": 4}), self.service.calls)

    def test_unstructured_or_command_like_payloads_are_not_forwarded(self):
        self.login()
        headers = self.headers()
        for updates in (
            {"action": "run; touch /tmp/owned"}, {"config": "arbitrary shell"},
            {"idempotency_key": ""}, {"idempotency_key": "x" * 129}, {"command": "whoami"},
        ):
            response = self.client.post(PREFIX + "/jobs", json={**self.job_body(), **updates}, headers=headers)
            self.assertEqual(400, response.status_code)
        for body in ({}, {"config": []}, {"config": {}, "command": "whoami"}):
            self.assertEqual(400, self.client.post(PREFIX + "/plans", json=body, headers=headers).status_code)
        self.assertFalse(any(call[0] in ("create_job", "preview_plan") for call in self.service.calls))

    def test_job_id_is_canonical_and_invalid_id_never_reaches_service(self):
        self.login()
        response = self.client.get(PREFIX + "/jobs/" + JOB_ID)
        self.assertEqual(JOB_ID, response.json["job"]["id"])
        for invalid in ("not-a-job", "a" * 31, "a" * 33, "g" * 32, "A" * 32):
            self.assertEqual(400, self.client.get(PREFIX + "/jobs/" + invalid).status_code)
        calls = [call for call in self.service.calls if call[0] == "get_job"]
        self.assertEqual([("get_job", OWNER, JOB_ID)], calls)

    def test_artifact_download_is_authenticated_and_service_allowlisted(self):
        self.login()
        response = self.client.get(PREFIX + "/jobs/" + JOB_ID + "/artifacts/result.json")
        self.assertEqual(200, response.status_code)
        self.assertEqual({"valid": True}, json.loads(response.data))
        self.assertIn("experiment-result.json", response.headers["Content-Disposition"])
        self.assert_private(response)
        response.close()
        self.assertEqual(404, self.client.get(PREFIX + "/jobs/" + JOB_ID + "/artifacts/unknown.json").status_code)

    def test_asset_and_artifact_path_traversal_is_rejected(self):
        self.login()
        (self.web / "outside.json").symlink_to(self.root / "result.json")
        for path in (
            "/ui/../result.json", "/ui/%2e%2e/result.json", "/ui/a%5c..%5cresult.json",
            "/ui/outside.json", "/jobs/" + JOB_ID + "/artifacts/../result.json",
            "/jobs/" + JOB_ID + "/artifacts/a%5c..%5cresult.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(400, self.client.get(PREFIX + path).status_code)
        self.assertFalse(any(call[0] == "get_artifact" for call in self.service.calls))

    def test_service_errors_are_bounded_and_internal_errors_do_not_leak(self):
        self.login()
        for failure, status in (
            (ValueError("Invalid worker count"), 400), (RuntimeError("Host is busy"), 409),
            (FileNotFoundError("private/path"), 404), (PermissionError("private/path"), 403),
            (Exception("private secret stack trace"), 500),
        ):
            self.service.failure = failure
            response = self.client.get(PREFIX + "/state")
            self.assertEqual(status, response.status_code)
            self.assertNotIn(b"private", response.data)
            self.assertNotIn(b"Traceback", response.data)
            self.assert_private(response)

    def test_unknown_private_routes_are_not_cached(self):
        response = self.client.get(PREFIX + "/does-not-exist")
        self.assertEqual(404, response.status_code)
        self.assert_private(response)

    def test_string_secret_and_explicit_secondary_https_origin_are_supported(self):
        application = Flask("string-secret-adapter")
        service = FakeService(self.root)
        service.csrf_secret = "fixture-string-secret-at-least-32-bytes"
        register_lab(
            application, fixture_login_required, [OWNER], service, self.web,
            user_exists=lambda _user: True,
            allowed_origins=("https://yche.me", "https://www.yche.me"),
        )
        client = application.test_client()
        client.set_cookie("auth_token", "owner-session-a")
        token = client.get(PREFIX + "/catalog").json["csrf_token"]
        response = client.post(
            PREFIX + "/plans", json={"config": {}},
            headers={"Origin": "https://www.yche.me", "X-CSRF-Token": token},
        )
        self.assertEqual(200, response.status_code)

    def test_registration_rejects_empty_or_ambiguous_security_configuration(self):
        for users, origins, secret in (
            ([], ["https://yche.me"], FakeService.csrf_secret),
            (OWNER, ["https://yche.me"], FakeService.csrf_secret),
            ([OWNER], [], FakeService.csrf_secret),
            ([OWNER], ["http://yche.me"], FakeService.csrf_secret),
            ([OWNER], ["https://yche.me/"], FakeService.csrf_secret),
            ([OWNER], ["https://yche.me"], b"short"),
        ):
            with self.subTest(users=users, origins=origins):
                app = Flask("invalid-config")
                service = FakeService(self.root)
                service.csrf_secret = secret
                with self.assertRaises(ValueError):
                    register_lab(app, fixture_login_required, users, service, self.web, allowed_origins=origins)


if __name__ == "__main__":
    unittest.main()
