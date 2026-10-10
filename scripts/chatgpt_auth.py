#!/usr/bin/env python3
"""Authorize llmbot to use an eligible ChatGPT plan without an API key."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

import jwt
import requests


AUTHORIZATION_ENDPOINT = "https://auth.openai.com/api/accounts/authorize"
TOKEN_ENDPOINT = "https://auth.openai.com/api/accounts/oauth/token"
OIDC_CONFIGURATION = "https://auth.openai.com/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
AGENT_NAME = "llmbot"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _write_json_0600(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _load_existing(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read existing credentials {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Existing credentials {path} must contain a JSON object")
    return payload


class _CallbackState:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.query: dict[str, list[str]] = {}


def _callback_handler(callback_state: _CallbackState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/auth/callback":
                self.send_response(404)
                self.end_headers()
                return
            callback_state.query = parse_qs(parsed.query)
            callback_state.event.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                (
                    "<h1>llmbot authorization received</h1>"
                    "<p>You can close this tab and return to the terminal.</p>"
                ).encode("utf-8")
            )

        def log_message(self, _format: str, *_args) -> None:
            return

    return Handler


def _validate_id_token(id_token: str, client_id: str, nonce: str) -> dict:
    metadata_response = requests.get(OIDC_CONFIGURATION, timeout=30)
    metadata_response.raise_for_status()
    metadata = metadata_response.json()
    jwks_client = jwt.PyJWKClient(metadata["jwks_uri"])
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    algorithms = metadata.get("id_token_signing_alg_values_supported") or ["RS256"]
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=algorithms,
        audience=client_id,
        issuer=metadata["issuer"],
    )
    if claims.get("nonce") != nonce:
        raise RuntimeError("OpenAI ID token nonce does not match this login attempt")
    return claims


def _select_host_id(existing: dict, requested_host_id: str | None) -> str:
    stored_host_id = str(existing.get("ext_agent_host_id") or "").strip()
    requested = str(requested_host_id or "").strip()
    if requested and stored_host_id and requested != stored_host_id:
        raise RuntimeError(
            "The requested host ID differs from the one already stored in credentials"
        )
    return requested or stored_host_id or f"urn:uuid:{uuid4()}"


def _validate_returning_identity(existing: dict, claims: dict) -> None:
    if not existing.get("client_id"):
        return
    existing_subject = str(existing.get("subject") or "").strip()
    returned_subject = str(claims.get("sub") or "").strip()
    if not existing_subject:
        raise RuntimeError("Existing credentials do not contain a validated subject")
    if not returned_subject or returned_subject != existing_subject:
        raise RuntimeError("OpenAI returned a different ChatGPT account identity")


def authorize(
    output_path: Path,
    timeout_seconds: int,
    no_browser: bool,
    host_id: str | None = None,
) -> dict:
    existing = _load_existing(output_path)
    host_id = _select_host_id(existing, host_id)
    existing_client_id = str(existing.get("client_id") or "").strip()
    registration_client_id = existing_client_id or DYNAMIC_CLIENT_ID

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())

    callback_state = _CallbackState()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), _callback_handler(callback_state)
    )
    redirect_uri = f"http://127.0.0.1:{server.server_port}/auth/callback"
    params = {
        "client_id": registration_client_id,
        "ext_agent_host_id": host_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    if not existing_client_id:
        params["agent_name_hint"] = AGENT_NAME
    elif existing.get("email"):
        params["login_hint"] = existing["email"]

    authorization_url = f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        print("Open this URL if the browser does not open automatically:")
        print(authorization_url)
        if not no_browser:
            webbrowser.open(authorization_url)
        if not callback_state.event.wait(timeout_seconds):
            raise RuntimeError("Timed out waiting for the OpenAI OAuth callback")
    finally:
        server.shutdown()
        server.server_close()

    query = callback_state.query
    if query.get("state", [""])[0] != state:
        raise RuntimeError("OAuth callback state does not match this login attempt")
    if query.get("error"):
        error = query["error"][0]
        description = query.get("error_description", [""])[0]
        raise RuntimeError(f"OpenAI authorization failed: {error}: {description}")
    code = query.get("code", [""])[0]
    if not code:
        raise RuntimeError("OAuth callback did not contain an authorization code")

    callback_client_id = query.get("client_id", [""])[0]
    if existing_client_id:
        if callback_client_id and callback_client_id != existing_client_id:
            raise RuntimeError("OAuth callback returned a different registered client_id")
        issued_client_id = existing_client_id
    else:
        issued_client_id = callback_client_id
        if not issued_client_id or issued_client_id == DYNAMIC_CLIENT_ID:
            raise RuntimeError("Dynamic registration did not return an issued client_id")

    token_response = requests.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "client_id": issued_client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "resource": RESOURCE,
        },
        timeout=30,
    )
    if token_response.status_code != 200:
        raise RuntimeError(
            f"OpenAI token exchange failed ({token_response.status_code}): "
            f"{token_response.text[:500]}"
        )
    tokens = token_response.json()
    id_token = str(tokens.get("id_token") or "")
    if not id_token:
        raise RuntimeError("OpenAI token response did not contain id_token")
    claims = _validate_id_token(id_token, issued_client_id, nonce)
    _validate_returning_identity(existing, claims)
    scopes = {
        scope
        for scope in str(tokens.get("scope") or query.get("scope", [""])[0]).split()
        if scope
    }
    if "chatgpt.tokens.use.direct" not in scopes:
        raise RuntimeError(
            "The selected account did not grant ChatGPT plan usage "
            "(missing chatgpt.tokens.use.direct)"
        )
    if not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise RuntimeError("OpenAI token response is missing renewable credentials")

    credentials = {
        "email": str(claims.get("email") or ""),
        "issuer": str(claims.get("iss") or "https://auth.openai.com"),
        "subject": str(claims.get("sub") or ""),
        "client_id": issued_client_id,
        "ext_agent_host_id": host_id,
        "id_token": id_token,
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "token_type": tokens.get("token_type", "Bearer"),
        "expires_in": int(tokens.get("expires_in") or 3600),
        "scopes": sorted(scopes),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json_0600(output_path, credentials)
    return credentials


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Authorize llmbot to use a ChatGPT Plus/Pro plan via OAuth."
    )
    parser.add_argument(
        "--output",
        default="chatgpt_credentials.json",
        help="Protected credential JSON path (default: chatgpt_credentials.json)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Seconds to wait for the local OAuth callback",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the authorization URL without opening the system browser",
    )
    parser.add_argument(
        "--host-id",
        help=(
            "Stable host identifier for a remote deployment; omit to create or "
            "reuse the value stored in the credential file"
        ),
    )
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve()
    try:
        credentials = authorize(
            output_path,
            max(30, args.timeout),
            args.no_browser,
            args.host_id,
        )
    except Exception as exc:
        print(f"Authorization failed: {exc}", file=sys.stderr)
        return 1
    email = credentials.get("email") or "selected ChatGPT account"
    print(f"Authorized {email}")
    print(f"Credentials saved to {output_path} with owner-only permissions")
    print("Do not paste this file or its tokens into chats, logs, or source control.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
