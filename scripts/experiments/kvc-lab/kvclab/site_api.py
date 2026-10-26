"""Authenticated HTTP adapter for the persistent experiment service."""

import hashlib
import hmac
import json
import re
import time
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, g, jsonify, make_response, request, send_file


MAX_JSON_BYTES = 64 * 1024
_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
_ACTION = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_LOGIN_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>请先登录 · yche.me</title><main><h1>请先登录</h1>
<p>请先在 yche.me 首页登录，随后返回并刷新此页。</p>
<a href="/">前往首页登录</a></main></html>"""


def _existing_site_user(username):
    from database import get_db

    database = get_db()
    try:
        return database.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone() is not None
    finally:
        database.close()


def _safe_name(value):
    return (
        isinstance(value, str)
        and bool(value)
        and "\\" not in value
        and "\x00" not in value
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )


def register_lab(
    app,
    login_required,
    allowed_users,
    service,
    web_root,
    user_exists=None,
    allowed_origins=("https://yche.me",),
    prefix="/site-api/kvc-lab",
):
    """Register private UI and API routes using the site's existing cookie login."""
    if isinstance(allowed_users, (str, bytes)):
        raise ValueError("allowed_users must be an account collection")
    users = frozenset(allowed_users)
    if not users or any(not isinstance(user, str) or not user for user in users):
        raise ValueError("A nonempty account allowlist is required")
    origins = frozenset(allowed_origins)
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Allowed origins must be exact HTTPS origins")
    if not origins:
        raise ValueError("An allowed origin is required")
    secret = service.csrf_secret
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("csrf_secret must contain at least 32 bytes")
    if not prefix.startswith("/") or prefix.endswith("/"):
        raise ValueError("Invalid API prefix")
    root = Path(web_root).resolve(strict=True)
    exists = user_exists or _existing_site_user
    blueprint = Blueprint("kvc_lab", __name__, url_prefix=prefix)

    def error(message, status):
        return jsonify({"error": message}), status

    def protected(html_entry=False):
        def decorate(view):
            @wraps(view)
            def account_checked(*args, **kwargs):
                username = getattr(g, "username", None)
                if not isinstance(username, str) or username not in users:
                    return error("Account is not authorized", 403)
                try:
                    if not exists(username):
                        return error("Account is not authorized", 403)
                    return view(*args, **kwargs)
                except FileNotFoundError:
                    return error("Resource not found", 404)
                except PermissionError:
                    return error("Access denied", 403)
                except ValueError as exc:
                    return error(str(exc)[:500] or "Invalid request", 400)
                except RuntimeError as exc:
                    return error(str(exc)[:500] or "Request conflicts with current state", 409)
                except Exception as exc:
                    app.logger.error("kvc-lab request failed (%s)", type(exc).__name__)
                    return error("Request failed", 500)

            authenticate = login_required(account_checked)

            @wraps(view)
            def wrapped(*args, **kwargs):
                response = make_response(authenticate(*args, **kwargs))
                if html_entry and response.status_code == 401:
                    response = make_response(_LOGIN_PAGE, 401)
                    response.mimetype = "text/html"
                return response

            return wrapped

        return decorate

    def token_for(username, bucket):
        cookie = request.cookies.get("auth_token", "")
        cookie_digest = hashlib.sha256(cookie.encode("utf-8")).hexdigest()
        payload = json.dumps([username, cookie_digest, bucket], separators=(",", ":"))
        signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return str(bucket) + "." + signature

    def post_data():
        if request.headers.get("Origin") not in origins:
            return None, error("Origin is not allowed", 403)
        supplied = request.headers.get("X-CSRF-Token", "")
        bucket = int(time.time() // 3600)
        if not supplied.isascii() or len(supplied) > 128 or not any(
            hmac.compare_digest(supplied, token_for(g.username, candidate))
            for candidate in (bucket, bucket - 1)
        ):
            return None, error("CSRF token is invalid or expired", 403)
        if request.mimetype != "application/json":
            return None, error("Content-Type must be application/json", 415)
        if request.headers.get("Content-Encoding", "identity").lower() != "identity":
            return None, error("Encoded request bodies are not supported", 415)
        if request.content_length is not None and request.content_length > MAX_JSON_BYTES:
            return None, error("Request body exceeds 64 KiB", 413)
        raw = request.stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            return None, error("Request body exceeds 64 KiB", 413)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            return None, error("Invalid JSON body", 400)
        if not isinstance(data, dict):
            return None, error("Request body must be a JSON object", 400)
        return data, None

    def ui_file(name):
        if not _safe_name(name):
            return error("Invalid asset path", 400)
        try:
            path = (root / name).resolve(strict=True)
            path.relative_to(root)
        except ValueError:
            return error("Invalid asset path", 400)
        if not path.is_file():
            return error("Resource not found", 404)
        return send_file(path, conditional=False, etag=False, max_age=0)

    @blueprint.route("/ui/", methods=["GET"], provide_automatic_options=False)
    @protected(html_entry=True)
    def ui_index():
        return ui_file("index.html")

    @blueprint.route("/ui/<path:asset>", methods=["GET"], provide_automatic_options=False)
    @protected(html_entry=True)
    def ui_asset(asset):
        return ui_file(asset)

    @blueprint.route("/guide.html", methods=["GET"], provide_automatic_options=False)
    @protected(html_entry=True)
    def guide():
        return ui_file("guide.html")

    @blueprint.route("/catalog", methods=["GET"], provide_automatic_options=False)
    @protected()
    def catalog():
        result = dict(service.catalog(g.username))
        result["csrf_token"] = token_for(g.username, int(time.time() // 3600))
        return jsonify(result)

    @blueprint.route("/state", methods=["GET"], provide_automatic_options=False)
    @protected()
    def state():
        return jsonify(service.state(g.username))

    @blueprint.route("/plans", methods=["POST"], provide_automatic_options=False)
    @protected()
    def plans():
        data, failure = post_data()
        if failure is not None:
            return failure
        if set(data) != {"config"} or not isinstance(data["config"], dict):
            return error("Expected a config object", 400)
        return jsonify({"plan": service.preview_plan(data["config"])})

    @blueprint.route("/jobs", methods=["POST"], provide_automatic_options=False)
    @protected()
    def create_job():
        data, failure = post_data()
        if failure is not None:
            return failure
        if set(data) != {"action", "config", "idempotency_key"}:
            return error("Expected action, config and idempotency_key", 400)
        action = data["action"]
        key = data["idempotency_key"]
        if not isinstance(action, str) or _ACTION.fullmatch(action) is None:
            return error("Invalid action", 400)
        if not isinstance(data["config"], dict):
            return error("Expected a config object", 400)
        if not isinstance(key, str) or _IDEMPOTENCY_KEY.fullmatch(key) is None:
            return error("Invalid idempotency_key", 400)
        return jsonify({"job": service.create_job(g.username, action, data["config"], key)})

    @blueprint.route("/jobs/<job_id>", methods=["GET"], provide_automatic_options=False)
    @protected()
    def get_job(job_id):
        if _JOB_ID.fullmatch(job_id) is None:
            return error("Invalid job id", 400)
        return jsonify({"job": service.get_job(g.username, job_id)})

    @blueprint.route(
        "/jobs/<job_id>/artifacts/<path:name>", methods=["GET"], provide_automatic_options=False
    )
    @protected()
    def artifact(job_id, name):
        if _JOB_ID.fullmatch(job_id) is None:
            return error("Invalid job id", 400)
        if not _safe_name(name):
            return error("Invalid artifact name", 400)
        path, download_name = service.get_artifact(g.username, job_id, name)
        return send_file(
            Path(path), as_attachment=True, download_name=download_name,
            conditional=False, etag=False, max_age=0,
        )

    @app.after_request
    def private_response(response):
        if request.path == prefix or request.path.startswith(prefix + "/"):
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.vary.add("Cookie")
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.register_blueprint(blueprint)
    return blueprint
