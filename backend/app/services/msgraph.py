"""Microsoft Entra ID (Azure AD) + Microsoft Graph mail integration.

Two ways to authorize, both configured in Settings → Integrations:

* delegated  ("Connect with Microsoft"): an admin signs in as the mailbox user (or a user
  with access to the shared mailboxes). OAuth 2.0 authorization-code flow with PKCE and a
  confidential client (client secret). We keep a refresh token (encrypted) and renew it.
  Scopes: offline_access openid profile email User.Read Mail.ReadWrite Mail.Send
          Mail.ReadWrite.Shared Mail.Send.Shared
* application (app-only): client-credentials flow with application permissions Mail.ReadWrite
  and Mail.Send, granted by an admin via the admin-consent link. Limit the app to the
  scheduling mailboxes with an Exchange application access policy.

Both use the same redirect URI:  {PORTAL_BASE_URL}/api/integrations/microsoft/callback

Every Graph call goes through `graph()`: timeouts, retries on 429/5xx (honouring
Retry-After), and classification into GraphError kinds. An invalid/expired grant marks
the connection `reauth_required` instead of failing silently.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import IntegrationConnection, OAuthState
from app.services import crypto

log = logging.getLogger("docshealth.msgraph")

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
DELEGATED_SCOPES = ["offline_access", "openid", "profile", "email", "User.Read", "Mail.ReadWrite", "Mail.Send",
                    "Mail.ReadWrite.Shared", "Mail.Send.Shared"]
APP_SCOPE = "https://graph.microsoft.com/.default"
STATE_TTL = timedelta(minutes=10)

# Tests inject an httpx.MockTransport here; production uses the real network.
transport: httpx.BaseTransport | None = None
_token_cache: dict[int, tuple[str, float]] = {}
_lock = threading.Lock()


class GraphError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None, transient: bool = False):
        super().__init__(message)
        self.kind, self.status, self.transient = kind, status, transient


@dataclass
class TokenResult:
    access_token: str
    expires_in: int
    refresh_token: str | None = None
    scope: str | None = None


def redirect_uri() -> str:
    return f"{get_settings().portal_base_url.rstrip('/')}/api/integrations/microsoft/callback"


def _client(timeout: float = 30) -> httpx.Client:
    return httpx.Client(timeout=timeout, transport=transport)


def get_connection(db: Session, create: bool = False) -> IntegrationConnection | None:
    conn = db.scalar(select(IntegrationConnection).where(IntegrationConnection.provider == "microsoft"))
    if conn is None and create:
        conn = IntegrationConnection(provider="microsoft", status="not_configured", auth_mode="delegated",
                                     poll_interval_seconds=120)
        db.add(conn)
        db.flush()
    return conn


def _secret(conn: IntegrationConnection) -> str:
    try:
        value = crypto.decrypt(conn.client_secret_encrypted)
    except crypto.CryptoError as exc:
        raise GraphError("config", f"Stored client secret can't be decrypted: {exc}") from exc
    if not value:
        raise GraphError("config", "Client secret is not set")
    return value


def _require_config(conn: IntegrationConnection | None) -> IntegrationConnection:
    if conn is None or not (conn.tenant_id and conn.client_id and conn.client_secret_encrypted):
        raise GraphError("config", "Enter the Tenant ID, Client ID and Client secret first")
    return conn


# --------------------------------------------------------------------------- OAuth: sign-in / consent

def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def start_authorization(db: Session, *, actor: str) -> str:
    """Return the Microsoft URL to send the admin's browser to (sign-in or admin consent)."""
    conn = _require_config(get_connection(db))
    state = secrets.token_urlsafe(32)
    row = OAuthState(state_hash=hashlib.sha256(state.encode()).hexdigest(), provider="microsoft", created_by=actor,
                     expires_at=datetime.now(timezone.utc) + STATE_TTL,
                     flow="admin_consent" if conn.auth_mode == "application" else "authorize")
    if conn.auth_mode == "application":
        db.add(row)
        db.flush()
        return f"{LOGIN}/{conn.tenant_id}/adminconsent?" + urlencode(
            {"client_id": conn.client_id, "redirect_uri": redirect_uri(), "state": state})
    verifier, challenge = _pkce()
    row.code_verifier_encrypted = crypto.encrypt(verifier)
    db.add(row)
    db.flush()
    return f"{LOGIN}/{conn.tenant_id}/oauth2/v2.0/authorize?" + urlencode({
        "client_id": conn.client_id, "response_type": "code", "redirect_uri": redirect_uri(),
        "response_mode": "query", "scope": " ".join(DELEGATED_SCOPES), "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
    })


def _consume_state(db: Session, state: str | None) -> OAuthState:
    if not state:
        raise GraphError("state", "Missing state; start the connection again from Settings → Integrations")
    row = db.scalar(select(OAuthState).where(OAuthState.state_hash == hashlib.sha256(state.encode()).hexdigest()))
    if row is None:
        raise GraphError("state", "Unknown sign-in attempt; start again from Settings → Integrations")
    if row.used_at is not None:
        raise GraphError("state", "This sign-in link was already used")
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise GraphError("state", "The sign-in took too long (over 10 minutes); start again")
    row.used_at = datetime.now(timezone.utc)
    return row


def _token_request(conn: IntegrationConnection, data: dict) -> TokenResult:
    body = {"client_id": conn.client_id, "client_secret": _secret(conn), **data}
    try:
        with _client() as c:
            r = c.post(f"{LOGIN}/{conn.tenant_id}/oauth2/v2.0/token", data=body)
    except httpx.HTTPError as exc:
        raise GraphError("connection", f"Could not reach Microsoft sign-in: {exc}", transient=True) from exc
    if r.status_code != 200:
        try:
            err = r.json()
        except ValueError:
            err = {}
        code, desc = err.get("error", "error"), (err.get("error_description") or r.text)[:300]
        kind = "reauth" if code in ("invalid_grant", "interaction_required") else (
            "auth" if code in ("invalid_client", "unauthorized_client", "invalid_request") else "token")
        hint = {
            "invalid_client": " Check the Client secret (value, not the secret ID) and that it hasn't expired.",
            "unauthorized_client": " Check the Client ID and that the app is enabled for this tenant.",
        }.get(code, "")
        raise GraphError(kind, f"Microsoft sign-in error {code}: {desc}{hint}", r.status_code)
    j = r.json()
    return TokenResult(j["access_token"], int(j.get("expires_in", 3600)), j.get("refresh_token"), j.get("scope"))


def complete_authorization(db: Session, *, state: str | None, code: str | None = None, admin_consent: str | None = None,
                           error: str | None = None, error_description: str | None = None) -> IntegrationConnection:
    """Handle the redirect back from Microsoft. Raises GraphError with a readable reason."""
    row = _consume_state(db, state)
    conn = _require_config(get_connection(db))
    if error:
        conn.status, conn.last_error = "error", f"{error}: {(error_description or '')[:300]}"
        raise GraphError("denied", f"Microsoft returned {error}: {(error_description or '').splitlines()[0][:200]}")
    if row.flow == "admin_consent":
        if (admin_consent or "").lower() != "true":
            raise GraphError("denied", "Admin consent was not granted")
        tok = _token_request(conn, {"grant_type": "client_credentials", "scope": APP_SCOPE})
        conn.refresh_token_encrypted = None
        conn.connected_account, conn.connected_name = f"app:{conn.client_id}", "Application permissions"
        conn.scopes = "application: Mail.ReadWrite Mail.Send"
        _cache(conn, tok)
    else:
        if not code:
            raise GraphError("denied", "No authorization code returned")
        verifier = crypto.decrypt(row.code_verifier_encrypted)
        tok = _token_request(conn, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
                                    "code_verifier": verifier, "scope": " ".join(DELEGATED_SCOPES)})
        if not tok.refresh_token:
            raise GraphError("token", "Microsoft didn't return a refresh token (is offline_access allowed?)")
        conn.refresh_token_encrypted = crypto.encrypt(tok.refresh_token)
        conn.scopes = tok.scope
        _cache(conn, tok)
        me = graph(db, "GET", "/me?$select=displayName,mail,userPrincipalName", conn=conn)
        conn.connected_account = me.get("mail") or me.get("userPrincipalName")
        conn.connected_name = me.get("displayName")
    conn.status, conn.last_error = "connected", None
    conn.connected_by, conn.connected_at = row.created_by, datetime.now(timezone.utc)
    return conn


def disconnect(db: Session, conn: IntegrationConnection) -> None:
    conn.refresh_token_encrypted = None
    conn.token_expires_at = None
    conn.connected_account = conn.connected_name = conn.scopes = None
    conn.status = "configured" if conn.client_secret_encrypted else "not_configured"
    _token_cache.pop(conn.id, None)


# --------------------------------------------------------------------------- access tokens

def _cache(conn: IntegrationConnection, tok: TokenResult) -> None:
    conn.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=tok.expires_in)
    with _lock:
        _token_cache[conn.id] = (tok.access_token, time.time() + tok.expires_in - 120)


def access_token(db: Session, conn: IntegrationConnection) -> str:
    with _lock:
        cached = _token_cache.get(conn.id)
    if cached and cached[1] > time.time():
        return cached[0]
    _require_config(conn)
    if conn.auth_mode == "application":
        tok = _token_request(conn, {"grant_type": "client_credentials", "scope": APP_SCOPE})
    else:
        if not conn.refresh_token_encrypted:
            raise GraphError("reauth", "Not connected: click 'Connect with Microsoft' in Settings → Integrations")
        try:
            refresh = crypto.decrypt(conn.refresh_token_encrypted)
            tok = _token_request(conn, {"grant_type": "refresh_token", "refresh_token": refresh,
                                        "scope": " ".join(DELEGATED_SCOPES)})
        except GraphError as exc:
            if exc.kind == "reauth":
                conn.status, conn.last_error = "reauth_required", str(exc)
            raise
        if tok.refresh_token:  # Microsoft rotates refresh tokens
            conn.refresh_token_encrypted = crypto.encrypt(tok.refresh_token)
    _cache(conn, tok)
    return tok.access_token


# --------------------------------------------------------------------------- Graph requests

def graph(db: Session, method: str, path: str, *, conn: IntegrationConnection | None = None, json=None,
          params=None, attempts: int = 3, sleep=time.sleep) -> dict:
    conn = conn or get_connection(db)
    if conn is None:
        raise GraphError("config", "Microsoft integration is not configured")
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    last: GraphError | None = None
    for attempt in range(1, attempts + 1):
        headers = {"Authorization": f"Bearer {access_token(db, conn)}"}
        try:
            with _client() as c:
                r = c.request(method, url, headers=headers, json=json, params=params)
        except httpx.TimeoutException:
            last = GraphError("timeout", "Microsoft Graph timed out", transient=True)
        except httpx.HTTPError as exc:
            last = GraphError("connection", f"Microsoft Graph network error: {exc}", transient=True)
        else:
            if r.status_code < 300:
                return r.json() if r.content and "json" in r.headers.get("content-type", "") else {}
            detail = _graph_error_text(r)
            if r.status_code == 401:
                _token_cache.pop(conn.id, None)  # stale token: refresh once, then give up
                last = GraphError("auth", f"Graph rejected the token: {detail}", 401, transient=attempt == 1)
            elif r.status_code == 403:
                last = GraphError("forbidden", f"Access denied: {detail}. Check the Mail permissions/admin consent and "
                                               "that the signed-in user (or app access policy) can use this mailbox.", 403)
            elif r.status_code == 404:
                last = GraphError("not_found", f"Not found: {detail}. Check the mailbox address/folder.", 404)
            elif r.status_code == 429 or r.status_code >= 500:
                last = GraphError("throttled" if r.status_code == 429 else "server",
                                  f"Microsoft Graph {r.status_code}: {detail}", r.status_code, transient=True)
                wait = r.headers.get("Retry-After")
                if attempt < attempts:
                    sleep(min(30.0, float(wait)) if wait and wait.isdigit() else 2 ** attempt)
                continue
            else:
                last = GraphError("rejected", f"Microsoft Graph {r.status_code}: {detail}", r.status_code)
        if not last.transient or attempt == attempts:
            break
        if last.kind in ("timeout", "connection"):
            sleep(2 ** attempt)
    raise last


def _graph_error_text(r: httpx.Response) -> str:
    try:
        e = r.json().get("error", {})
        return f"{e.get('code', '')} {e.get('message', '')}".strip()[:300]
    except ValueError:
        return r.text[:300]


# --------------------------------------------------------------------------- mailbox operations

def _folder_path(mailbox: str, folder: str) -> str:
    well_known = {"inbox": "inbox", "junkemail": "junkemail", "archive": "archive"}
    key = folder.replace(" ", "").lower()
    if key in well_known:
        return f"/users/{mailbox}/mailFolders/{well_known[key]}"
    return f"/users/{mailbox}/mailFolders/{folder}"  # folder id


def resolve_folder(db: Session, mailbox: str, folder: str) -> str:
    """Folder display name or id -> Graph path (searches top-level and Inbox subfolders)."""
    key = folder.replace(" ", "").lower()
    if key in ("inbox", "junkemail", "archive"):
        return _folder_path(mailbox, folder)
    for parent in (f"/users/{mailbox}/mailFolders", f"/users/{mailbox}/mailFolders/inbox/childFolders"):
        found = graph(db, "GET", parent, params={"$filter": f"displayName eq '{folder.replace(chr(39), chr(39) * 2)}'",
                                                 "$select": "id,displayName"})
        if found.get("value"):
            return f"/users/{mailbox}/mailFolders/{found['value'][0]['id']}"
    raise GraphError("not_found", f"Folder '{folder}' not found in {mailbox}")


def folder_info(db: Session, mailbox: str, folder: str) -> dict:
    path = resolve_folder(db, mailbox, folder)
    return graph(db, "GET", path, params={"$select": "displayName,unreadItemCount,totalItemCount"})


def list_unread(db: Session, mailbox: str, folder: str, top: int = 25, since: datetime | None = None) -> list[dict]:
    """Oldest-first unread mail, optionally only mail received at/after `since` (the mailbox cursor)."""
    path = resolve_folder(db, mailbox, folder)
    flt = "isRead eq false"
    if since is not None:
        since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        flt = f"receivedDateTime ge {since.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ} and isRead eq false"
    data = graph(db, "GET", f"{path}/messages", params={
        "$filter": flt, "$top": top, "$orderby": "receivedDateTime asc",
        "$select": "id,internetMessageId,conversationId,subject,body,from,receivedDateTime,internetMessageHeaders"})
    return data.get("value", [])


def mark_read(db: Session, mailbox: str, message_id: str) -> None:
    graph(db, "PATCH", f"/users/{mailbox}/messages/{message_id}", json={"isRead": True})


def reply_in_thread(db: Session, mailbox: str, message_id: str, body_text: str) -> dict:
    """Reply to the original message from the same mailbox so it stays in the patient's mail chain
    (Graph sets In-Reply-To/References/conversation). Returns the sent draft's ids."""
    draft = graph(db, "POST", f"/users/{mailbox}/messages/{message_id}/createReply", json={})
    draft_id = draft["id"]
    try:
        graph(db, "PATCH", f"/users/{mailbox}/messages/{draft_id}",
              json={"body": {"contentType": "Text", "content": body_text}})
        graph(db, "POST", f"/users/{mailbox}/messages/{draft_id}/send")
    except GraphError:
        try:  # don't leave a half-written draft behind
            graph(db, "DELETE", f"/users/{mailbox}/messages/{draft_id}", attempts=1)
        except GraphError:
            log.warning("could not delete reply draft %s in %s", draft_id, mailbox)
        raise
    return {"draft_id": draft_id, "conversation_id": draft.get("conversationId"),
            "internet_message_id": draft.get("internetMessageId")}


def send_new(db: Session, mailbox: str, to: str, subject: str, body_text: str) -> None:
    graph(db, "POST", f"/users/{mailbox}/sendMail", json={
        "message": {"subject": subject, "body": {"contentType": "Text", "content": body_text},
                    "toRecipients": [{"emailAddress": {"address": to}}]},
        "saveToSentItems": True})
