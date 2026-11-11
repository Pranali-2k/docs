"""Entra ID integration end to end against a simulated Microsoft (login + Graph):
configure → connect (PKCE) → callback → tokens encrypted → mailbox polling → AI pipeline →
reply from the same mailbox in the same thread → token refresh/rotation → reauth."""
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.main import app
from app.models import EmailMessage, IntegrationConnection, PortalRequest
from app.services import msgraph

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MAILBOX = "scheduling@clinic.example.com"


class FakeMicrosoft:
    """Just enough of login.microsoftonline.com and graph.microsoft.com."""

    def __init__(self):
        self.calls, self.refresh_count, self.revoked = [], 0, False
        self.inbox = [{
            "id": "MSG-1", "internetMessageId": "<p1@gmail.com>", "conversationId": "CONV-1",
            "subject": "Appointment request", "receivedDateTime": "2026-10-06T09:00:00Z",
            "from": {"emailAddress": {"address": "maria.gonzalez@example.com", "name": "Maria Gonzalez"}},
            "body": {"contentType": "text", "content": "Hi, I'd like to schedule my annual physical with Dr. Rao next "
                                                       "Tuesday morning. DOB 03/14/1985.\nThanks,\nMaria Gonzalez"},
            "internetMessageHeaders": []}, {
            "id": "MSG-2", "internetMessageId": "<auto@x>", "conversationId": "CONV-2", "subject": "Out of office",
            "receivedDateTime": "2026-10-06T09:01:00Z",
            "from": {"emailAddress": {"address": "someone@example.com"}}, "body": {"content": "away"},
            "internetMessageHeaders": [{"name": "Auto-Submitted", "value": "auto-replied"}]}]
        self.read, self.sent = set(), []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        path, host = req.url.path, req.url.host
        if host == "login.microsoftonline.com" and path.endswith("/token"):
            form = parse_qs(req.content.decode())
            grant = form["grant_type"][0]
            if self.revoked and grant == "refresh_token":
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "AADSTS70008 expired"})
            if form["client_secret"][0] != "s3cret-value":
                return httpx.Response(401, json={"error": "invalid_client", "error_description": "bad secret"})
            if grant == "authorization_code":
                assert form["code_verifier"][0] and form["redirect_uri"][0].endswith("/api/integrations/microsoft/callback")
            if grant == "refresh_token":
                self.refresh_count += 1
            return httpx.Response(200, json={"access_token": f"AT-{len(self.calls)}", "expires_in": 3600,
                                             "refresh_token": f"RT-{self.refresh_count}",
                                             "scope": "Mail.ReadWrite Mail.Send"})
        if path == "/v1.0/me":
            return httpx.Response(200, json={"mail": MAILBOX, "displayName": "Scheduling Desk"})
        if path.endswith("/mailFolders/inbox") and req.method == "GET":
            return httpx.Response(200, json={"displayName": "Inbox", "unreadItemCount": 2, "totalItemCount": 9})
        if path.endswith("/mailFolders/inbox/messages"):
            return httpx.Response(200, json={"value": [m for m in self.inbox if m["id"] not in self.read]})
        if req.method == "PATCH" and "/messages/" in path and json.loads(req.content).get("isRead"):
            self.read.add(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={})
        if path.endswith("/createReply"):
            return httpx.Response(201, json={"id": "DRAFT-1", "conversationId": "CONV-1"})
        if req.method == "PATCH" and path.endswith("/messages/DRAFT-1"):
            self.sent.append(("body", json.loads(req.content)["body"]["content"]))
            return httpx.Response(200, json={})
        if path.endswith("/messages/DRAFT-1/send"):
            self.sent.append(("send", path))
            return httpx.Response(202)
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": path}})


@pytest.fixture
def ms(monkeypatch):
    fake = FakeMicrosoft()
    monkeypatch.setattr(msgraph, "transport", httpx.MockTransport(fake))
    msgraph._token_cache.clear()
    yield fake
    msgraph._token_cache.clear()


@pytest.fixture
def admin(db):
    s = get_settings()
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password}).json()
    c.headers["Authorization"] = f"Bearer {tok['access_token']}"
    return c


def configure(admin, **over):
    body = {"auth_mode": "delegated", "tenant_id": TENANT, "client_id": CLIENT, "client_secret": "s3cret-value",
            "poll_interval_seconds": 60}
    body.update(over)
    return admin.put("/api/integrations/microsoft", json=body)


def connect(admin):
    url = admin.post("/api/integrations/microsoft/connect").json()["authorize_url"]
    q = parse_qs(urlparse(url).query)
    cb = admin.get("/api/integrations/microsoft/callback", params={"code": "AUTH-CODE", "state": q["state"][0]},
                   follow_redirects=False)
    return url, q, cb


def test_only_admins_and_validation(client, admin):
    assert client.get("/api/integrations/microsoft").status_code == 403
    assert configure(admin, client_id="not-a-guid-not-a-guid-not-a-guid-xx").status_code == 422
    assert configure(admin, client_secret=None).status_code == 422  # no secret saved yet


def test_redirect_uri_and_connect_flow(admin, ms, db):
    view = configure(admin).json()
    assert view["redirect_uri"] == f"{get_settings().portal_base_url}/api/integrations/microsoft/callback"
    assert "s3cret-value" not in json.dumps(view) and view["connection"]["client_secret_hint"] == "alue"
    url, q, cb = connect(admin)
    assert url.startswith(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize")
    assert q["client_id"] == [CLIENT] and q["code_challenge_method"] == ["S256"] and "offline_access" in q["scope"][0]
    assert cb.status_code == 303 and cb.headers["location"].endswith("/settings/integrations?ms=connected")
    conn = admin.get("/api/integrations/microsoft").json()["connection"]
    assert conn["status"] == "connected" and conn["connected_account"] == MAILBOX
    db.expire_all()
    row = db.scalar(select(IntegrationConnection))
    assert row.refresh_token_encrypted and "RT-" not in row.refresh_token_encrypted  # encrypted at rest
    # the state is single-use
    again = admin.get("/api/integrations/microsoft/callback", params={"code": "X", "state": q["state"][0]},
                      follow_redirects=False)
    assert "ms=error" in again.headers["location"] and "already+used" in again.headers["location"]


def test_bad_state_and_denied_consent_are_reported(admin, ms):
    configure(admin)
    bad = admin.get("/api/integrations/microsoft/callback", params={"code": "X", "state": "forged"}, follow_redirects=False)
    assert "ms=error" in bad.headers["location"]
    url = admin.post("/api/integrations/microsoft/connect").json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    denied = admin.get("/api/integrations/microsoft/callback", follow_redirects=False,
                       params={"error": "access_denied", "error_description": "User declined", "state": state})
    assert "ms=error" in denied.headers["location"] and "access_denied" in denied.headers["location"]


def test_wrong_secret_is_explained(admin, ms):
    configure(admin, client_secret="wrong")
    _, _, cb = connect(admin)
    assert "invalid_client" in cb.headers["location"]


def test_application_mode_admin_consent(admin, ms):
    configure(admin, auth_mode="application")
    url = admin.post("/api/integrations/microsoft/connect").json()["authorize_url"]
    assert f"/{TENANT}/adminconsent?" in url
    state = parse_qs(urlparse(url).query)["state"][0]
    cb = admin.get("/api/integrations/microsoft/callback", params={"admin_consent": "True", "tenant": TENANT,
                                                                  "state": state}, follow_redirects=False)
    assert cb.headers["location"].endswith("ms=connected")
    assert admin.post("/api/integrations/microsoft/test").json()["ok"]


def test_mailbox_poll_creates_requests_and_replies_in_thread(admin, ms, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "email_outbound_mode", "outbox")  # prove the thread reply bypasses it
    configure(admin)
    connect(admin)
    mbs = admin.post("/api/integrations/mailboxes", json={"address": MAILBOX, "folder": "Inbox"}).json()["mailboxes"]
    mid = mbs[0]["id"]
    t = admin.post(f"/api/integrations/mailboxes/{mid}/test").json()
    assert t["ok"] and t["unread"] == 2

    r = admin.post(f"/api/integrations/mailboxes/{mid}/poll").json()
    assert r["ok"] and len(r["created"]) == 1 and r["skipped"] == 1  # out-of-office auto-reply ignored
    assert ms.read == {"MSG-1", "MSG-2"}

    db.expire_all()
    req = db.scalar(select(PortalRequest).where(PortalRequest.reference == r["created"][0]))
    src = req.source_email
    assert src.mailbox_id == mid and src.provider_message_id == "MSG-1" and src.conversation_id == "CONV-1"
    # the AI auto-booked and replied from the SAME mailbox, in the SAME thread (createReply on MSG-1)
    out = db.scalars(select(EmailMessage).where(EmailMessage.request_id == req.id,
                                                EmailMessage.direction == "outbound")).all()
    assert out and out[0].send_status == "sent" and out[0].delivery_method == "graph-reply"
    assert out[0].from_address == MAILBOX and out[0].conversation_id == "CONV-1"
    assert any(c.url.path.endswith("/messages/MSG-1/createReply") for c in ms.calls)
    assert ("send", f"/v1.0/users/{MAILBOX}/messages/DRAFT-1/send") in ms.sent

    # staff "Send only" also replies in-thread
    s = get_settings()
    staff = TestClient(app)
    tok = staff.post("/api/auth/login", json={"email": s.seed_scheduler_email, "password": s.seed_scheduler_password}).json()
    staff.headers["Authorization"] = f"Bearer {tok['access_token']}"
    sent = staff.post(f"/api/requests/{req.id}/send-response", json={"body": "See you Tuesday!", "resolve": False}).json()
    assert sent["email"]["send_status"] == "sent" and sent["email"]["delivery_method"] == "graph-reply"
    assert ("body", "See you Tuesday!") in ms.sent

    # polling again creates nothing new (already read / de-duplicated)
    assert admin.post(f"/api/integrations/mailboxes/{mid}/poll").json()["created"] == []


def test_refresh_token_rotation_and_reauth(admin, ms, db):
    configure(admin)
    connect(admin)
    msgraph._token_cache.clear()
    assert admin.post("/api/integrations/microsoft/test").json()["ok"]
    assert ms.refresh_count == 1
    ms.revoked = True
    msgraph._token_cache.clear()
    r = admin.post("/api/integrations/microsoft/test").json()
    assert not r["ok"] and r["error_kind"] == "reauth"
    assert admin.get("/api/integrations/microsoft").json()["connection"]["status"] == "reauth_required"


def test_failed_thread_reply_can_be_retried(admin, ms, db, monkeypatch):
    configure(admin)
    connect(admin)
    mid = admin.post("/api/integrations/mailboxes", json={"address": MAILBOX}).json()["mailboxes"][0]["id"]
    admin.patch(f"/api/integrations/mailboxes/{mid}", json={"process_inbound": False})
    created = admin.post(f"/api/integrations/mailboxes/{mid}/poll").json()["created"]
    req = db.scalar(select(PortalRequest).where(PortalRequest.reference == created[0]))

    original = ms.__call__
    monkeypatch.setattr(FakeMicrosoft, "__call__", lambda self, r: httpx.Response(503) if r.url.path.endswith("/createReply") else original(r))
    msgraph.transport = httpx.MockTransport(lambda r: FakeMicrosoft.__call__(ms, r))
    monkeypatch.setattr("time.sleep", lambda _: None)
    out = admin.post(f"/api/requests/{req.id}/send-response", json={"body": "Hello", "resolve": False}).json()["email"]
    assert out["send_status"] == "failed" and "503" in out["send_error"]

    monkeypatch.setattr(FakeMicrosoft, "__call__", original.__func__)
    msgraph.transport = httpx.MockTransport(ms)
    again = admin.post(f"/api/emails/{out['id']}/resend").json()
    assert again["send_status"] == "sent" and again["delivery_method"] == "graph-reply"


def test_read_only_permission_still_imports_and_advances_cursor(admin, ms, db, monkeypatch):
    """With only Mail.Read, marking as read is forbidden: import continues, a clear note is shown,
    and the received-time cursor moves on so the next check doesn't re-read old mail."""
    configure(admin)
    connect(admin)
    mid = admin.post("/api/integrations/mailboxes", json={"address": MAILBOX}).json()["mailboxes"][0]["id"]
    admin.patch(f"/api/integrations/mailboxes/{mid}", json={"process_inbound": False})
    original = FakeMicrosoft.__call__

    def forbid_patch(self, req):
        if req.method == "PATCH" and "/messages/MSG-" in req.url.path:
            return httpx.Response(403, json={"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}})
        return original(self, req)

    monkeypatch.setattr(FakeMicrosoft, "__call__", forbid_patch)
    msgraph.transport = httpx.MockTransport(lambda r: FakeMicrosoft.__call__(ms, r))
    r = admin.post(f"/api/integrations/mailboxes/{mid}/poll").json()
    assert r["ok"] and len(r["created"]) == 1 and r["failed"] == 0
    mb = next(m for m in r["integration"]["mailboxes"] if m["id"] == mid)
    assert "Mail.ReadWrite" in mb["last_error"]
    second = [c for c in ms.calls if c.url.path.endswith("/mailFolders/inbox/messages")]
    admin.post(f"/api/integrations/mailboxes/{mid}/poll")
    last_list = [c for c in ms.calls if c.url.path.endswith("/mailFolders/inbox/messages")][-1]
    assert last_list.url.params["$filter"].startswith("receivedDateTime ge 2026-10-06T09:01:00Z")  # cursor applied
    assert len(second) >= 1


def test_background_sync_drains_a_backlog_in_one_cycle(admin, ms, db, monkeypatch):
    """A mailbox with more unread mail than one page (25) is caught up in a single cycle, oldest first."""
    from datetime import datetime, timedelta, timezone

    from app.services import mailbox_sync

    configure(admin)
    connect(admin)
    mid = admin.post("/api/integrations/mailboxes", json={"address": MAILBOX}).json()["mailboxes"][0]["id"]
    admin.patch(f"/api/integrations/mailboxes/{mid}", json={"process_inbound": False})
    start = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)
    backlog = [{"id": f"BL-{i}", "internetMessageId": f"<bl{i}@example.com>", "conversationId": f"C-{i}",
                "subject": f"Request {i}", "receivedDateTime": (start + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "from": {"emailAddress": {"address": f"patient{i}@example.com"}}, "body": {"content": "Please call me"},
                "internetMessageHeaders": []} for i in range(60)]

    def fake_list(db_, mailbox, folder, top=25, since=None):
        since = since if since is None or since.tzinfo else since.replace(tzinfo=timezone.utc)
        rows = [m for m in backlog if since is None
                or datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")) >= since]
        return rows[:top]

    monkeypatch.setattr(msgraph, "list_unread", fake_list)
    results = mailbox_sync.run_due(db)
    assert sum(len(r["created"]) for r in results) == 60 and len(results) == 3
    assert db.scalar(select(func.count()).select_from(EmailMessage).where(EmailMessage.mailbox_id == mid)) == 60


def test_internal_senders_are_skipped_and_counts_reach_the_dashboard(admin, ms, db):
    """Mail from the clinic's own domain is not a patient request; folder counts (Mail.Read) show on the dashboard."""
    ms.inbox.append({
        "id": "MSG-3", "internetMessageId": "<staff@clinic>", "conversationId": "CONV-3", "subject": "Lunch?",
        "receivedDateTime": "2026-10-06T09:02:00Z",
        "from": {"emailAddress": {"address": "front.desk@Clinic.Example.com"}}, "body": {"content": "Pizza at 1?"},
        "internetMessageHeaders": []})
    configure(admin)
    connect(admin)
    mb = admin.post("/api/integrations/mailboxes", json={"address": MAILBOX}).json()["mailboxes"][0]
    assert mb["skip_sender_domains"] == ["clinic.example.com"]  # defaults to the mailbox's own domain
    admin.patch(f"/api/integrations/mailboxes/{mb['id']}", json={"process_inbound": False})

    r = admin.post(f"/api/integrations/mailboxes/{mb['id']}/poll").json()
    assert len(r["created"]) == 1 and r["skipped"] == 2 and r["skipped_internal"] == 1
    assert db.scalar(select(EmailMessage).where(EmailMessage.message_id == "<staff@clinic>")) is None
    row = next(m for m in r["integration"]["mailboxes"] if m["id"] == mb["id"])
    assert (row["unread_count"], row["read_count"], row["total_count"]) == (2, 7, 9)
    assert row["skipped_internal_total"] == 1

    dash = admin.get("/api/dashboard/summary").json()["mailboxes"]
    assert dash[0]["address"] == MAILBOX and (dash[0]["unread"], dash[0]["read"], dash[0]["total"]) == (2, 7, 9)
    assert dash[0]["imported"] == 1 and dash[0]["skipped_internal"] == 1

    # editable list: validated, and clearing it turns the filter off
    bad = admin.patch(f"/api/integrations/mailboxes/{mb['id']}", json={"skip_sender_domains": ["not a domain"]})
    assert bad.status_code == 422
    ok = admin.patch(f"/api/integrations/mailboxes/{mb['id']}", json={"skip_sender_domains": ["@Partner.org", "boss@x.com"]}).json()
    assert ok["mailboxes"][0]["skip_sender_domains"] == ["partner.org", "boss@x.com"]


def test_sender_filter_matching():
    from app.services.mailbox_sync import parse_skip_list, sender_filtered

    skip = parse_skip_list("docshealth.com; @Partner.org, boss@x.com")
    assert sender_filtered("Shawn.Boyd@docshealth.com", skip)
    assert sender_filtered("a@mail.docshealth.com", skip)  # subdomain
    assert sender_filtered("boss@x.com", skip) and not sender_filtered("other@x.com", skip)
    assert not sender_filtered("patient@gmail.com", skip)
    assert not sender_filtered("someone@notdocshealth.com", skip)  # suffix but not a subdomain


def test_skipped_mail_is_counted_once_however_often_the_mailbox_is_checked(admin, ms, db, monkeypatch):
    """With only Mail.Read the same unread mail is seen on every check; counts must stay distinct."""
    ms.inbox.append({
        "id": "MSG-INT", "internetMessageId": "<int@clinic>", "conversationId": "C-INT", "subject": "staff note",
        "receivedDateTime": "2026-10-06T09:05:00Z", "from": {"emailAddress": {"address": "nurse@clinic.example.com"}},
        "body": {"content": "fyi"}, "internetMessageHeaders": []})
    configure(admin)
    connect(admin)
    mid = admin.post("/api/integrations/mailboxes", json={"address": MAILBOX}).json()["mailboxes"][0]["id"]
    admin.patch(f"/api/integrations/mailboxes/{mid}", json={"process_inbound": False})
    original = FakeMicrosoft.__call__

    def keep_unread(self, req):  # Mail.Read only: PATCH isRead is denied, cursor uses "ge" so the boundary repeats
        if req.method == "PATCH" and "/messages/MSG-" in req.url.path:
            return httpx.Response(403, json={"error": {"code": "ErrorAccessDenied", "message": "denied"}})
        return original(self, req)

    monkeypatch.setattr(FakeMicrosoft, "__call__", keep_unread)
    msgraph.transport = httpx.MockTransport(lambda r: FakeMicrosoft.__call__(ms, r))
    for _ in range(4):
        admin.post(f"/api/integrations/mailboxes/{mid}/poll")
    dash = admin.get("/api/dashboard/summary").json()["mailboxes"][0]
    assert dash["skipped_internal"] == 1 and dash["skipped_other"] == 1 and dash["imported"] == 1


def test_test_mode_never_replies_in_the_patients_thread(admin, ms, db):
    configure(admin)
    connect(admin)
    admin.put("/api/integrations/outbound/test-mode", json={"enabled": True, "recipient": "tester@example.org"})
    mid = admin.post("/api/integrations/mailboxes", json={"address": MAILBOX}).json()["mailboxes"][0]["id"]
    r = admin.post(f"/api/integrations/mailboxes/{mid}/poll").json()
    req = db.scalar(select(PortalRequest).where(PortalRequest.reference == r["created"][0]))
    out = db.scalars(select(EmailMessage).where(EmailMessage.request_id == req.id, EmailMessage.direction == "outbound")).all()
    assert out and all(m.send_status == "awaiting_approval" and m.redirect_to == "tester@example.org" for m in out)
    assert not any(c.url.path.endswith("/createReply") for c in ms.calls) and ms.sent == []
