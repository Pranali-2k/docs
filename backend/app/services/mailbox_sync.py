"""Process the configured Microsoft 365 mailboxes (Settings → Integrations).

poll_mailbox(): unread mail in the mailbox's folder → portal requests (AI pipeline runs),
remembering the Graph message id + conversation so replies go back in the same thread
from the same mailbox. One bad message never stops the batch; mailbox-level problems are
stored on the mailbox (`last_error`) and shown in the UI.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IntegrationMailbox, MailboxSkippedMessage
from app.services import audit, msgraph
from app.services.email_gateway import _skip_reason, ingest_email

log = logging.getLogger("docshealth.mailboxes")


def own_addresses(db: Session) -> set[str]:
    addrs = {m.address.lower() for m in db.scalars(select(IntegrationMailbox))}
    conn = msgraph.get_connection(db)
    if conn and conn.connected_account and "@" in conn.connected_account:
        addrs.add(conn.connected_account.lower())
    return addrs


def parse_skip_list(value: str | None) -> list[str]:
    """'docshealth.com, @x.org; a@b.com' -> ['docshealth.com', 'x.org', 'a@b.com'] (lower-case, de-duplicated)."""
    out: list[str] = []
    for part in (value or "").replace(";", ",").replace("\n", ",").split(","):
        item = part.strip().lower().lstrip("@")
        if item and item not in out:
            out.append(item)
    return out


def sender_filtered(address: str, skip: list[str]) -> str | None:
    """Reason if the sender matches the mailbox's skip list (exact address, domain or any subdomain)."""
    address = address.lower()
    domain = address.rsplit("@", 1)[-1]
    for item in skip:
        if "@" in item:
            if address == item:
                return f"sender {item} is on the skip list"
        elif domain == item or domain.endswith("." + item):
            return f"internal sender (@{item})"
    return None


def record_skip(db: Session, mb: IntegrationMailbox, message_id: str, kind: str, reason: str,
                address: str | None, received: datetime | None) -> bool:
    """Remember a skipped message once. Returns True the first time it is seen."""
    exists = db.scalar(select(MailboxSkippedMessage.id).where(
        MailboxSkippedMessage.mailbox_id == mb.id, MailboxSkippedMessage.provider_message_id == message_id))
    if exists:
        return False
    db.add(MailboxSkippedMessage(mailbox_id=mb.id, provider_message_id=message_id, kind=kind, reason=reason[:300],
                                 from_address=address, received_at=received))
    db.flush()
    return True


def skip_counts(db: Session, mailbox_id: int) -> dict[str, int]:
    from sqlalchemy import func

    rows = db.execute(select(MailboxSkippedMessage.kind, func.count()).where(
        MailboxSkippedMessage.mailbox_id == mailbox_id).group_by(MailboxSkippedMessage.kind)).all()
    return {k: n for k, n in rows}


def refresh_counts(db: Session, mb: IntegrationMailbox) -> dict | None:
    """Store the folder's unread/total counts (needs only Mail.Read). Never raises."""
    try:
        info = msgraph.folder_info(db, mb.address, mb.folder)
    except Exception as exc:  # counts are informational; the poll itself must not fail because of them
        log.warning("mailbox %s: could not read folder counts (%s)", mb.address, exc)
        return None
    mb.unread_count = info.get("unreadItemCount")
    mb.total_count = info.get("totalItemCount")
    mb.counts_at = datetime.now(timezone.utc)
    return info


def poll_mailbox(db: Session, mb: IntegrationMailbox, *, actor: str = "mailbox-sync", limit: int = 25) -> dict:
    from app.services import pipeline  # late import: pipeline imports email_gateway

    created, skipped, failed, mark_read_blocked = [], 0, 0, False
    internal = 0
    own = own_addresses(db)
    skip_list = parse_skip_list(mb.skip_sender_domains)
    try:
        messages = msgraph.list_unread(db, mb.address, mb.folder, top=limit, since=mb.last_received_at)
    except msgraph.GraphError as exc:
        mb.last_error, mb.last_polled_at = str(exc), datetime.now(timezone.utc)
        db.commit()
        raise
    newest = mb.last_received_at
    for m in messages:
        try:
            received = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00"))
            sender = (m.get("from") or {}).get("emailAddress") or {}
            address = (sender.get("address") or "unknown@unknown").lower()
            headers = {h["name"]: h["value"] for h in (m.get("internetMessageHeaders") or [])}
            internal_reason = sender_filtered(address, skip_list)
            reason = ("sent by one of the clinic's own mailboxes" if address in own
                      else internal_reason or _skip_reason(address, headers))
            if reason:
                kind = "own_mailbox" if address in own else "internal" if internal_reason else "automated"
                if record_skip(db, mb, m["id"], kind, reason, address, received):
                    skipped += 1
                    internal += 1 if kind == "internal" else 0
                db.commit()
                log.info("mailbox %s: skipped message from %s (%s)", mb.address, address, reason)
            else:
                req, is_new = ingest_email(
                    db, from_address=address, from_name=sender.get("name"), subject=m.get("subject") or "",
                    body=(m.get("body") or {}).get("content", ""), to_address=mb.address,
                    message_id=m.get("internetMessageId") or f"<{m['id']}@graph>",
                    in_reply_to=headers.get("In-Reply-To"),
                    received_at=received,
                    source="graph", actor=actor, mailbox_id=mb.id, provider_message_id=m["id"],
                    conversation_id=m.get("conversationId"))
                db.commit()
                if is_new and mb.process_inbound:
                    try:
                        pipeline.process_request(db, req, actor="ai-assistant")
                        db.commit()
                    except Exception:  # request stays in the inbox for staff
                        db.rollback()
                        log.exception("pipeline failed for request %s", req.id)
                if is_new:
                    created.append(req.reference)
            if mb.mark_as_read and not mark_read_blocked:
                try:
                    msgraph.mark_read(db, mb.address, m["id"])
                except msgraph.GraphError as exc:
                    if exc.kind != "forbidden":
                        raise
                    # e.g. only Mail.Read granted: keep going; de-duplication by Message-ID prevents re-imports
                    mark_read_blocked = True
                    log.warning("mailbox %s: can't mark mail as read (%s)", mb.address, exc)
            if newest is None or received > (newest if newest.tzinfo else newest.replace(tzinfo=timezone.utc)):
                newest = received  # advance only past messages handled successfully
        except Exception:  # leave it unread so the next poll retries it
            db.rollback()
            failed += 1
            log.exception("mailbox %s: could not process message %s", mb.address, m.get("id"))
    mb.last_polled_at, mb.last_poll_count = datetime.now(timezone.utc), len(created)
    mb.skipped_internal_total = skip_counts(db, mb.id).get("internal", 0)  # distinct, never double-counted
    refresh_counts(db, mb)
    if not failed:  # on failures keep the cursor so the failed message is retried next time
        mb.last_received_at = newest
    notes = []
    if failed:
        notes.append(f"{failed} message(s) could not be processed; they stay unread and will be retried")
    if mark_read_blocked:
        notes.append("Can't mark mail as read: grant Mail.ReadWrite (already-imported mail is skipped automatically)")
    mb.last_error = "; ".join(notes) or None
    audit.record(db, "mailbox", mb.id, "mailbox_polled", actor, address=mb.address, created=len(created),
                 skipped=skipped, skipped_internal=internal, failed=failed)
    db.commit()
    return {"mailbox": mb.address, "created": created, "skipped": skipped, "skipped_internal": internal, "failed": failed,
            "more": len(messages) >= limit and not failed}


def run_due(db: Session) -> list[dict]:
    """Poll every enabled mailbox whose interval has elapsed (called by the background loop)."""
    conn = msgraph.get_connection(db)
    if conn is None or conn.status != "connected":
        return []
    interval = max(30, conn.poll_interval_seconds or 120)
    now = datetime.now(timezone.utc)
    results = []
    for mb in db.scalars(select(IntegrationMailbox).where(IntegrationMailbox.enabled.is_(True))):
        last = mb.last_polled_at
        if last is not None:
            last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < interval:
                continue
        try:
            for _ in range(8):  # drain a backlog in pages of 25, bounded per cycle
                result = poll_mailbox(db, mb)
                results.append(result)
                if not result.get("more"):
                    break
        except msgraph.GraphError as exc:
            log.warning("mailbox %s poll failed: %s", mb.address, exc)
            if exc.kind == "reauth":
                db.commit()
                break
    return results
